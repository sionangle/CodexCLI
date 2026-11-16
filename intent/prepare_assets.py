"""Resolve upstream-pinned binary dependencies into the project build cache."""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORK = REPO / ".intent-work"
sys.path.insert(0, str(REPO / "scripts"))
os.environ["CODEX_REPO_ROOT"] = str(REPO)

from codex_package.ripgrep import resolve_rg_bin
from codex_package.targets import TARGET_SPECS
from codex_package.v8 import resolve_codex_v8_cargo_env


def prepare():
    cache = WORK / "assets"
    cache.mkdir(parents=True, exist_ok=True)
    # Upstream's DotSlash downloader uses the process temp directory as its cache root.
    tempfile.tempdir = str(cache)
    target = TARGET_SPECS["x86_64-pc-windows-msvc"]
    print("Checking upstream-pinned V8 archive, bindings and checksums...", flush=True)
    # Always verify against this checkout's pins, including when an earlier build
    # left V8 override variables in the invoking shell.
    environment = resolve_codex_v8_cargo_env(target, environ={}, cache_root=cache)
    print("Checking upstream-pinned ripgrep...", flush=True)
    result = {"environment": environment, "ripgrep": str(resolve_rg_bin(target, None))}
    (WORK / "assets.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    prepare()
