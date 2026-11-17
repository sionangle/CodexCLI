"""Build a complete candidate package; publishing requires separate validation."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from platform_support import tomllib
from datetime import datetime, timezone
from pathlib import Path

from prepare_assets import REPO, WORK, prepare
from codex_package.layout import build_package_dir, validate_package_dir
from codex_package.targets import PACKAGE_VARIANTS, TARGET_SPECS, PackageInputs
from lockfile import verify as verify_lockfile
from cache_state import refresh as refresh_cache
from platform_support import WINDOWS, digest_file, executable, native_target


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--profile", choices=["dev-small", "release"], default="dev-small")
    args = parser.parse_args()
    run = WORK / ("build-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    run.mkdir(parents=True)
    report = {"status": "building", "profile": args.profile, "package": str(run / "package")}
    try:
        git = ["git", "-c", "safe.directory=" + REPO.as_posix()]
        source_status = subprocess.check_output(git + ["status", "--porcelain"], cwd=REPO, text=True)
        if source_status:
            raise RuntimeError("Commit the reviewed custom changes before building a versioned candidate package")
        baseline = json.loads((REPO / "intent/baseline.json").read_text(encoding="utf-8-sig"))
        subprocess.run(git + ["merge-base", "--is-ancestor", baseline["commit"], "HEAD"], cwd=REPO, check=True)
        subprocess.run(git + ["diff", "--exit-code", baseline["commit"], "--",
                              "codex-rs/rust-toolchain.toml"], cwd=REPO, check=True)
        verify_lockfile()
        workspace = tomllib.loads((REPO / "codex-rs/Cargo.toml").read_text(encoding="utf-8"))
        upstream = workspace["workspace"]["package"]["version"]
        version = upstream + "+intent." + str(baseline["custom_revision"])
        if baseline["tag"] != "rust-v" + upstream:
            raise RuntimeError("Candidate baseline does not match workspace version")
        assets = prepare()
        environment = {**os.environ, **assets["environment"]}
        if not environment.get('CODEX_INTENT_CACHE_LEASE'):
            raise RuntimeError('Use intent/Build-Intent.ps1 to serialize access to the shared compiler cache.')
        environment["STABLE_GIT_COMMIT"] = subprocess.check_output(
            git + ["rev-parse", "HEAD"],
            cwd=REPO, text=True,
        ).strip()
        spec = TARGET_SPECS[native_target()]
        bins = ["codex", "codex-code-mode-host", "codex-exec-server"]
        bins += ["codex-command-runner", "codex-windows-sandbox-setup"] if WINDOWS else ["bwrap"]
        report['workspace_cache'] = refresh_cache(args.profile, run / 'workspace-cache.log')
        verify_lockfile()
        command = ["cargo", "build", "--locked", "--profile", args.profile, "--target", spec.target]
        for binary in bins:
            command += ["--bin", binary]
        report["command"] = command
        print("Building complete candidate; log: " + str(run / "cargo.log"), flush=True)
        with (run / "cargo.log").open("w", encoding="utf-8") as log:
            subprocess.run(command, cwd=REPO / "codex-rs", env=environment,
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        output = Path(environment["CARGO_TARGET_DIR"]) / spec.target / args.profile
        inputs = PackageInputs(
            entrypoint_bin=output / executable("codex"), code_mode_host_bin=output / executable("codex-code-mode-host"),
            exec_server_bin=output / executable("codex-exec-server"), rg_bin=Path(assets["ripgrep"]),
            bwrap_bin=None if WINDOWS else output / "bwrap",
            codex_command_runner_bin=output / "codex-command-runner.exe" if WINDOWS else None,
            codex_windows_sandbox_setup_bin=output / "codex-windows-sandbox-setup.exe" if WINDOWS else None,
        )
        package = run / "package"
        package.mkdir()
        # Preserve the upstream internal package layout for daemon/helper resolution.
        # Only the codex-intent entrypoint is installed onto the user's PATH.
        build_package_dir(package, version, PACKAGE_VARIANTS["codex"], spec, inputs)
        shutil.copy2(package / "bin" / executable("codex"), package / "bin" / executable("codex-intent"))
        shutil.copy2(REPO / "LICENSE", package / "LICENSE")
        shutil.copy2(REPO / "NOTICE", package / "NOTICE")
        validate_package_dir(package, PACKAGE_VARIANTS["codex"], spec)
        # --version also checks that the separately maintained revision stamp agrees
        # with package metadata before a candidate can enter release validation.
        validation_home = run / "codex-home"
        validation_home.mkdir()
        isolated_environment = {**environment, "CODEX_HOME": str(validation_home)}
        actual_version = subprocess.check_output(
            [str(package / "bin" / executable("codex-intent")), "--version"],
            env=isolated_environment, text=True,
        ).strip()
        if actual_version != "codex-intent " + version:
            raise RuntimeError("Compiled version does not match package metadata: " + actual_version)
        report.update(status="built-unvalidated", version=version, baseline=baseline,
                      commit=environment["STABLE_GIT_COMMIT"], target=spec.target)
        report["sha256"] = {}
        for path in package.rglob("*"):
            if path.is_file():
                report["sha256"][str(path.relative_to(package))] = digest_file(path)
        print("Candidate built, not published: " + str(package), flush=True)
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        (run / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Build report: " + str(run / "report.json"), flush=True)


if __name__ == "__main__":
    main()
