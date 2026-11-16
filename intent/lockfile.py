"""Reconcile release-tag path-package versions without resolving dependencies.

Some upstream release tags change workspace.package.version but leave their path
packages at 0.0.0 in Cargo.lock. Only those versions may differ from the tag;
sources, checksums, dependency edges, and all registry/Git versions must match.
"""

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


def expected_lock():
    baseline = json.loads((REPO / "intent/baseline.json").read_text(encoding="utf-8-sig"))
    original = subprocess.check_output(
        ["git", "-c", "safe.directory=" + REPO.as_posix(), "show",
         baseline["commit"] + ":codex-rs/Cargo.lock"], cwd=REPO,
    ).decode("utf-8").replace("\r\n", "\n")
    workspace = tomllib.loads((REPO / "codex-rs/Cargo.toml").read_text(encoding="utf-8"))
    version = workspace["workspace"]["package"]["version"]
    inherited_names = set()
    for manifest in (REPO / "codex-rs").rglob("Cargo.toml"):
        package = tomllib.loads(manifest.read_text(encoding="utf-8")).get("package", {})
        if package.get("version") == {"workspace": True}:
            inherited_names.add(package["name"])
    count = 0

    def normalize(match):
        nonlocal count
        block = match.group()
        package = tomllib.loads(block)["package"][0]
        if "source" not in package and package["name"] in inherited_names:
            if package["version"] != version:
                # Exact field replacement; dependency edges remain byte-for-byte intact.
                block = block.replace('version = "' + package["version"] + '"',
                                      'version = "' + version + '"', 1)
                count += 1
        return block

    normalized = re.sub(r"(?ms)^\[\[package\]\].*?(?=^\[\[package\]\]|\Z)", normalize, original)
    return normalized, count


def verify():
    expected, count = expected_lock()
    actual = (REPO / "codex-rs/Cargo.lock").read_text(encoding="utf-8")
    if actual != expected:
        raise RuntimeError("Cargo.lock differs from the upstream dependency lock plus permitted path-package version reconciliation")
    print(f"Upstream dependency lock verified; {count} local package version fields reconciled.")


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--normalize", action="store_true")
    args = parser.parse_args()
    if args.normalize:
        expected, _ = expected_lock()
        current = (REPO / "codex-rs/Cargo.lock").read_text(encoding="utf-8")
        # Never silently overwrite unrelated dependency changes.
        before = tomllib.loads(current)
        after = tomllib.loads(expected)
        for data in (before, after):
            for package in data["package"]:
                if "source" not in package:
                    package.pop("version", None)
        if before != after:
            raise RuntimeError("Refusing to overwrite unrelated lockfile changes")
        (REPO / "codex-rs/Cargo.lock").write_text(expected, encoding="utf-8", newline="\n")
    verify()


if __name__ == "__main__":
    main()
