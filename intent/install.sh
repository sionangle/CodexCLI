#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo 'This installer currently supports Linux x86_64 (Ubuntu 22.04 or compatible).' >&2
  exit 1
fi
intent_base="${CODEX_INTENT_RELEASE_BASE_URL:-https://github.com/sionangle/CodexCLI/releases/latest/download}"
intent_tmp="$(mktemp -d "${TMPDIR:-/tmp}/codex-intent-install.XXXXXXXX")"
trap 'rm -rf -- "$intent_tmp"' EXIT
intent_asset=codex-intent-linux-x64.tar.gz
echo 'Downloading codex-intent...'
curl --fail --location --retry 3 "$intent_base/$intent_asset" --output "$intent_tmp/package.tar.gz"
intent_expected="$(curl --fail --silent --show-error --location --retry 3 "$intent_base/$intent_asset.sha256")"
if [[ ! "$intent_expected" =~ ^[0-9a-f]{64}$ ]]; then
  echo 'The release checksum is invalid.' >&2
  exit 1
fi
intent_actual="$(sha256sum "$intent_tmp/package.tar.gz")"
if [[ "${intent_actual%% *}" != "$intent_expected" ]]; then
  echo 'The downloaded archive differs from the published SHA256. Installation stopped.' >&2
  exit 1
fi
mkdir "$intent_tmp/unpacked"
tar --extract --gzip --file "$intent_tmp/package.tar.gz" --directory "$intent_tmp/unpacked" --no-same-owner
intent_release="$intent_tmp/unpacked/codex-intent"
"$intent_release/runtime/bin/python3" -E -s -B -X utf8 "$intent_release/intent/install.py" "$@"
