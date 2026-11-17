"""Assemble a portable release only from matching, successful acceptance evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from managed_runtime import hashes, read
from platform_support import WINDOWS, native_target


def assemble(repo, build_dir, validation_dir, smoke_dir, terminal_dir, destination, stage=2, maintenance=None,
             maintenance_candidate=False):
    repo, destination = Path(repo).resolve(), Path(destination).resolve()
    build_dir, validation_dir, smoke_dir, terminal_dir = map(Path, [build_dir, validation_dir, smoke_dir, terminal_dir])
    build, validation, smoke, terminal = [read(path / 'report.json') for path in
                                        [build_dir, validation_dir, smoke_dir, terminal_dir]]
    git_command = ['git', '-c', 'safe.directory=' + repo.as_posix()]
    head = subprocess.check_output(git_command + ['rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    dirty = subprocess.check_output(git_command + ['status', '--porcelain'], cwd=repo, text=True)
    if head != build['commit'] or dirty:
        raise RuntimeError('Release assembly requires the same clean source commit as the candidate build.')
    if (build['status'] != 'built-unvalidated' or validation['status'] != 'passed-automated-only'
            or not validation['eligible_for_candidate_validation']
            or not validation['conditional_network_tests_enabled']
            or build['commit'] != validation['commit'] or build['commit'] != validation['commit_at_end']):
        raise RuntimeError('Build and clean source validation evidence do not match.')
    package = (build_dir / 'package').resolve()
    if smoke['status'] != 'passed-binary-smoke-only' or Path(smoke['package']).resolve() != package:
        raise RuntimeError('Binary smoke acceptance is missing or belongs to a different package.')
    if (terminal['status'] != 'passed-terminal-acceptance-only' or terminal['stage'] != 2
            or Path(terminal['package']).resolve() != package):
        raise RuntimeError('Complete intent and V2 terminal acceptance is required.')
    required = {'terminal_probe', 'normal_probe', 'failure_probe', 'resume_probe', 'v2_terminal_probe'}
    if {check['name'] for check in terminal['checks']} != required or any(check['exit_code'] for check in terminal['checks']):
        raise RuntimeError('A required terminal scenario is missing or failed.')
    normalize = lambda values: {key.replace('\\', '/'): value for key, value in values.items()}
    actual = hashes(package)
    if actual != normalize(build['sha256']) or actual != normalize(terminal['package_sha256']):
        raise RuntimeError('Accepted binaries have changed.')
    if stage == 3:
        if not maintenance:
            raise RuntimeError('Upgrade, idle protection and rollback acceptance is required.')
        evidence = read(Path(maintenance) / 'report.json')
        if (evidence['status'] != 'passed-maintenance-acceptance' or evidence['candidate_sha256'] != actual
                or evidence['source_commit'] != build['commit']
                or evidence.get('source_commit_at_end') != build['commit']
                or not evidence.get('source_clean_at_start') or not evidence.get('source_clean_at_end')
                or 'installed-maintenance-entry-survives-rollback-to-version-without-tools' not in evidence['checks']):
            raise RuntimeError('Maintenance acceptance does not match this candidate.')
    if destination.exists() or destination.with_name(destination.name + '.preparing').exists():
        raise RuntimeError('Delivery destination already exists; no release was overwritten.')
    staging = destination.with_name(destination.name + '.preparing')
    staging.mkdir(parents=True)
    shutil.copytree(package, staging / 'package')
    evidence_root = staging / 'validation'
    folders = [('build', build_dir), ('automated', validation_dir), ('binary', smoke_dir), ('terminal', terminal_dir)]
    folders.extend((check['name'], Path(check['directory'])) for check in terminal['checks'])
    if maintenance:
        folders.append(('maintenance', Path(maintenance)))
    for label, folder in folders:
        target = evidence_root / label
        target.mkdir(parents=True)
        for path in folder.iterdir():
            # Never ship probe authentication/session directories or model request bodies.
            if path.is_file() and (path.name == 'report.json' or path.suffix in ['.log', '.vt', '.txt']):
                shutil.copy2(path, target / path.name)
    shutil.copy2(repo / 'intent/baseline.json', staging / 'upstream-baseline.json')
    if stage == 3 or maintenance_candidate:
        shutil.copytree(repo / 'intent', staging / 'intent', ignore=shutil.ignore_patterns('__pycache__'))
        from prepare_runtime import prepare as prepare_runtime
        runtime = prepare_runtime(repo)
        shutil.copytree(runtime, staging / 'runtime')
        runtime_evidence = evidence_root / 'private-runtime'
        runtime_evidence.mkdir()
        shutil.copy2(runtime.parent / 'report.json', runtime_evidence / 'report.json')
    # Stage delivery remains directly executable; managed installation is a separate explicit
    # action so verification never touches the user's official or production data.
    if WINDOWS:
        launcher = '''[CmdletBinding(PositionalBinding = $false)]
    param([string]$DataHome = (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex-intent'),
          [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $ErrorActionPreference = 'Stop'
    $data = [IO.Path]::GetFullPath($DataHome).TrimEnd('\\')
    $official = [IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex')).TrimEnd('\\')
    if ($data -ieq $official -or $data.StartsWith($official + '\\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Use a separate codex-intent data directory.' }
    New-Item -ItemType Directory -Force -Path $data | Out-Null
    $file = Join-Path $data 'config.toml'
    if (-not (Test-Path -LiteralPath $file)) {
        $stream = [IO.File]::Open($file, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
        try { $bytes = [Text.Encoding]::UTF8.GetBytes("cli_auth_credentials_store = `"file`"`n[tui]`nintent_view = true`n"); $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
    }
    $original = $env:CODEX_HOME
    try { $env:CODEX_HOME = $data; & (Join-Path $PSScriptRoot 'package\\bin\\codex-intent.exe') @Arguments; $code = $LASTEXITCODE } finally { $env:CODEX_HOME = $original }
    exit $code
    '''
        (staging / 'Run-CodexIntent.ps1').write_text(launcher, encoding='utf-8-sig')
        (staging / 'codex-intent.cmd').write_text('@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run-CodexIntent.ps1" %*\r\n', encoding='ascii')
    else:
        shutil.copy2(repo / 'intent/portable_linux.py', staging / 'portable_linux.py')
        launcher = staging / 'codex-intent'
        launcher.write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
                            'intent_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"\n'
                            'intent_python="$intent_dir/runtime/bin/python3"\n'
                            'if [[ ! -x "$intent_python" ]]; then intent_python=python3; fi\n'
                            'exec "$intent_python" -E -s -B -X utf8 "$intent_dir/portable_linux.py" "$@"\n', encoding='utf-8')
        launcher.chmod(0o755)
    text = f'''codex-intent {'Windows' if WINDOWS else 'Linux (Ubuntu 22.04+)'} x64 — stage {stage}
Version: {build['version']}
Upstream: {build['baseline']['tag']} / {build['baseline']['commit']}
Custom source: {build['commit']}

运行 .\\codex-intent.cmd，查看版本 .\\codex-intent.cmd --version。
保留整个目录；不要单独复制 exe，不要将 package\\bin 加入 PATH。
默认独立数据：%USERPROFILE%\\.codex-intent；首次正式使用需单独登录。
/config -> Sessions -> Intent view / 意图视图，可持久切换。
Ctrl+T 主动打开完整记录。/subagents 打开子任务详情。
任务面板支持 V2 并行、嵌套、失败、中断、重新派发和恢复；缺失的说明与时间明确标示。
显示过滤不改变执行、模型结果或会话原始记录。

validation 包含实际检查报告和终端记录；原报告路径指向构建工作区。
构建 profile: {build['profile']}。当前不是 release 性能优化构建。
diff 和审批已通过 Rust 渲染及交互检查，尚未单独进行真实终端端到端验收。
测试使用隔离配置、会话和本地模拟模型，没有使用用户登录或付费模型。
'''
    if not WINDOWS:
        text = text.replace('运行 .\\codex-intent.cmd，查看版本 .\\codex-intent.cmd --version。',
                            '运行 ./codex-intent，查看版本 ./codex-intent --version。')
        text = text.replace('exe', '程序').replace('package\\bin', 'package/bin')
        text = text.replace('%USERPROFILE%\\.codex-intent', '~/.codex-intent（CODEX_INTENT_HOME 可覆盖）')
    if stage == 2:
        text += '\n第二阶段已交付；升级、切换和回滚工具仍在开发，尚未完成整个项目。\n'
        if maintenance_candidate:
            text += '此目录是维护验收的候选包，附带维护源码供实际测试；尚未通过维护发布门禁。\n'
    else:
        text += '\n维护与管理安装：参见 intent/MAINTENANCE.md。便携启动本身不参与管理安装的切换。\n'
    (staging / 'README.txt').write_text(text, encoding='utf-8-sig')
    release = {'status': 'verified-stage-2-portable' if stage == 2 else 'verified-custom-release',
               'scope': 'intent-view-and-v2-panel' if stage == 2 else 'custom-cli-and-maintenance',
               'version': build['version'], 'commit': build['commit'], 'upstream': build['baseline'],
               'profile': build['profile'], 'target': native_target(), 'features': ['intent-view', 'v2-task-panel'], 'sha256': hashes(staging)}
    if stage == 3:
        release['features'].append('managed-upgrade-and-rollback')
    (staging / 'release.json').write_text(json.dumps(release, ensure_ascii=False, indent=2), encoding='utf-8')
    staging.rename(destination)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    for name in ['repo', 'build', 'validation', 'smoke', 'terminal', 'destination']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--stage', type=int, choices=[2, 3], default=2)
    parser.add_argument('--maintenance', type=Path)
    parser.add_argument('--maintenance-candidate', action='store_true')
    args = parser.parse_args()
    print(assemble(args.repo, args.build, args.validation, args.smoke, args.terminal,
                   args.destination, args.stage, args.maintenance, args.maintenance_candidate))
