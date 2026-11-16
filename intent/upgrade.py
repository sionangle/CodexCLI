"""Isolated upstream replay. Conflicts and failed gates preserve their worktree and report."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import uuid
from urllib.request import Request, urlopen
from managed_runtime import read, write, stamp

OFFICIAL = 'https://github.com/openai/codex.git'
STABLE_TAG = re.compile(r'^rust-v(\d+)\.(\d+)\.(\d+)$')


def git(repo, *arguments, check=True):
    result = subprocess.run(['git', '-c', 'safe.directory=' + Path(repo).resolve().as_posix(),
                             '-c', 'core.safecrlf=false', '-c', 'core.longpaths=true', *arguments], cwd=repo,
                            capture_output=True, encoding='utf-8', errors='replace')
    if check and result.returncode:
        raise RuntimeError('Git ' + ' '.join(arguments) + ' failed with exit code ' + str(result.returncode)
                           + ':\n' + (result.stdout + result.stderr)[-16000:])
    return result


def require_source(repo):
    repo = Path(repo).resolve(strict=True)
    if git(repo, 'status', '--porcelain').stdout:
        raise RuntimeError('Commit the custom source changes before adapting or validating.')
    baseline = read(repo / 'intent/baseline.json')
    git(repo, 'merge-base', '--is-ancestor', baseline['commit'], 'HEAD')
    upstream = git(repo, 'remote', 'get-url', 'upstream').stdout.strip()
    if upstream.rstrip('/') not in [OFFICIAL, OFFICIAL[:-4]]:
        raise RuntimeError('The upstream remote must be the official openai/codex repository.')
    return repo, baseline


def validation_workspace(repo):
    """Short fixture root shared by linked worktrees, separate from all real user homes."""
    common = Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir').stdout.strip()).resolve(strict=True)
    if common.name != '.git':
        raise RuntimeError('Cannot establish the owning repository for isolated acceptance fixtures.')
    return common.parent.parent


def check_stable(repo):
    repo, baseline = require_source(repo)
    report = {'status': 'checking', 'checked_at': stamp(), 'upstream': OFFICIAL, 'baseline': baseline}
    folder = repo / '.intent-work/official-releases'
    folder.mkdir(parents=True, exist_ok=True)
    try:
        request = Request('https://api.github.com/repos/openai/codex/releases/latest',
                          headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'codex-intent-maintenance'})
        try:
            with urlopen(request, timeout=30) as response:
                latest = json.load(response)
            report['source'] = 'official-github-releases-api'
        except Exception as api_error:
            report['api_error'] = str(api_error)
            # GitHub's public latest-release redirect is independent of the REST quota. Only
            # an exact stable tag on the official repository is accepted; never guess from tags.
            with urlopen(Request('https://github.com/openai/codex/releases/latest',
                                 headers={'User-Agent': 'codex-intent-maintenance'}), timeout=30) as response:
                url = response.geturl()
            prefix = 'https://github.com/openai/codex/releases/tag/'
            if not url.startswith(prefix):
                raise RuntimeError('Official latest release did not resolve to a release tag.')
            latest = {'tag_name': url[len(prefix):], 'html_url': url, 'published_at': None}
            report['source'] = 'official-github-latest-release-redirect'
        match = STABLE_TAG.fullmatch(latest['tag_name'])
        if not match or latest.get('prerelease') or latest.get('draft'):
            raise RuntimeError('GitHub latest is not a recognized stable Rust release; no version was selected.')
        current = STABLE_TAG.fullmatch(baseline['tag'])
        report.update(status='checked', latest_stable_tag=latest['tag_name'], published_at=latest['published_at'],
                      url=latest['html_url'], newer=tuple(map(int, match.groups())) > tuple(map(int, current.groups())))
        return report
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        write(folder / (report['checked_at'] + '.json'), report)


def replay(folder, resume=False):
    folder = Path(folder).resolve(strict=True)
    plan_file = folder / 'report.json'
    plan = read(plan_file)
    worktree = Path(plan['worktree'])
    if plan.get('adapted_commit'):
        git(worktree, 'merge-base', '--is-ancestor', plan['adapted_commit'], 'HEAD')
        if git(worktree, 'status', '--porcelain').stdout:
            raise RuntimeError('Adapted worktree has uncommitted changes; retain and review them before validation.')
        return plan
    if resume and plan.get('pending'):
        pending = plan['pending']
        if git(worktree, 'rev-parse', '-q', '--verify', 'CHERRY_PICK_HEAD', check=False).returncode == 0:
            raise RuntimeError('Resolve and stage conflicts, then run git cherry-pick --continue in the retained worktree first.')
        if git(worktree, 'status', '--porcelain').stdout:
            raise RuntimeError('Resolved worktree must be clean.')
        head = git(worktree, 'rev-parse', 'HEAD').stdout.strip()
        parent = git(worktree, 'rev-parse', 'HEAD^').stdout.strip()
        if parent != pending['parent'] or head == pending['parent']:
            raise RuntimeError('Expected exactly one resolved replay commit. Skipping a patch is not accepted.')
        body = git(worktree, 'show', '-s', '--format=%B', 'HEAD').stdout
        if pending['original'] not in body:
            raise RuntimeError('Retain the original commit SHA in the resolved commit message for replay provenance.')
        plan['applied'].append({'original': pending['original'], 'replayed': head, 'conflict_resolved': True})
        plan['pending'] = None
    try:
        for commit in plan['patches'][len(plan['applied']):]:
            plan['pending'] = {'original': commit, 'parent': git(worktree, 'rev-parse', 'HEAD').stdout.strip()}
            plan['status'] = 'replaying'
            write(plan_file, plan)
            result = git(worktree, 'cherry-pick', '-x', commit, check=False)
            (folder / ('replay-' + commit[:12] + '.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
            if result.returncode:
                raise RuntimeError('Replay conflict or failure. The worktree and pending patch are retained: ' + str(worktree))
            plan['applied'].append({'original': commit, 'replayed': git(worktree, 'rev-parse', 'HEAD').stdout.strip()})
            plan['pending'] = None
            write(plan_file, plan)
        baseline = read(worktree / 'intent/baseline.json')
        revision = plan['custom_revision']
        toolchain = tomllib.loads((worktree / 'codex-rs/rust-toolchain.toml').read_text(encoding='utf-8'))['toolchain']['channel']
        baseline.update(tag=plan['tag'], commit=plan['target_commit'], rust_toolchain=toolchain,
                        custom_revision=revision, implementation_stage='upgrade-candidate', release_verified=False)
        write(worktree / 'intent/baseline.json', baseline)
        version_file = worktree / 'codex-rs/intent-version.rs'
        content, count = re.subn(r'\+intent\.\d+', '+intent.' + str(revision), version_file.read_text(encoding='utf-8'))
        if count != 1:
            raise RuntimeError('The shared custom version stamp needs manual adaptation.')
        version_file.write_text(content, encoding='utf-8')
        # This only reconciles local workspace versions; it refuses any changed external edge,
        # source, checksum or version relative to the target upstream's own lockfile.
        result = subprocess.run([sys.executable, str(worktree / 'intent/lockfile.py'), '--normalize'],
                                cwd=worktree, capture_output=True, encoding='utf-8')
        (folder / 'target-lock.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError('Target lock reconciliation failed. Restore target external dependencies and review the conflict.')
        git(worktree, 'diff', '--exit-code', plan['target_commit'], '--', 'codex-rs/rust-toolchain.toml')
        git(worktree, 'add', 'intent/baseline.json', 'codex-rs/intent-version.rs', 'codex-rs/Cargo.lock')
        git(worktree, 'commit', '-m', 'chore(intent): record adapted upstream baseline and custom revision')
        plan['adapted_commit'] = git(worktree, 'rev-parse', 'HEAD').stdout.strip()
        if plan.get('error'):
            plan.setdefault('previous_errors', []).append(plan.pop('error'))
        plan['status'] = 'awaiting-equivalence-review'
        review = {'target_commit': plan['target_commit'], 'decision': 'unreviewed', 'reason': '',
                  'rule': 'Keep custom features and all critical acceptance gates. Remove an equivalent patch only after source and behavior review.'}
        if plan['target_commit'] == plan['baseline']['commit']:
            review.update(decision='retain', reason='The target is the exact current upstream baseline; no new upstream implementation exists to replace these patches.')
            plan['status'] = 'ready-to-validate'
        write(folder / 'equivalence-review.json', review)
        return plan
    except Exception as error:
        plan.update(status='failed-no-publication', error=str(error))
        raise
    finally:
        write(plan_file, plan)


def adapt(repo, tag):
    repo, baseline = require_source(repo)
    if not STABLE_TAG.fullmatch(tag):
        raise RuntimeError('Use an exact official stable tag such as rust-v0.169.0.')
    folder = repo / '.intent-work/upgrades' / (tag + '-' + stamp())
    folder.mkdir(parents=True)
    plan = {'status': 'preparing', 'tag': tag, 'baseline': baseline,
            'custom_revision': baseline['custom_revision'] + 1, 'repository': str(repo), 'applied': []}
    try:
        result = git(repo, 'fetch', '--no-tags', 'upstream', 'refs/tags/' + tag)
        (folder / 'fetch.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        target = git(repo, 'rev-parse', 'FETCH_HEAD^{commit}').stdout.strip()
        head = git(repo, 'rev-parse', 'HEAD').stdout.strip()
        patches = git(repo, 'rev-list', '--reverse', baseline['commit'] + '..' + head).stdout.splitlines()
        if not patches or len(patches) > 50:
            raise RuntimeError('Review and consolidate the custom patch series before replay.')
        for commit in patches:
            if len(git(repo, 'rev-list', '--parents', '-n', '1', commit).stdout.split()) != 2:
                raise RuntimeError('The custom series must contain ordinary replayable commits, not merges.')
        # cargo fmt --all passes absolute source paths to rustfmt. A deeply nested checkout
        # can exceed Windows' command-line limit even with Git long-path support enabled.
        worktree = validation_workspace(repo) / ('iw-' + uuid.uuid4().hex[:8])
        plan.update(target_commit=target, source_commit=head, patches=patches, worktree=str(worktree))
        write(folder / 'report.json', plan)
        git(repo, 'worktree', 'add', '--detach', str(worktree), target)
        # The evidence is deliberately separate from the worktree so conflict resolution cannot
        # accidentally overwrite the prior working release or discard the replay report.
        changes = git(repo, 'log', '--format=%H %s', baseline['commit'] + '..' + target, '--',
                      'codex-rs/tui', 'codex-rs/core/src/tools/handlers/multi_agents_v2',
                      'codex-rs/protocol', 'codex-rs/app-server-protocol',
                      'codex-rs/app-server', 'codex-rs/app-server-daemon').stdout
        (folder / 'upstream-equivalence-candidates.log').write_text(changes, encoding='utf-8')
        write(folder / 'report.json', plan)
        return replay(folder)
    except Exception as error:
        # replay() owns its more precise pending-commit report once it has started.
        if (folder / 'report.json').exists():
            plan = read(folder / 'report.json')
        plan.update(status='failed-no-publication', error=str(error))
        write(folder / 'report.json', plan)
        raise
