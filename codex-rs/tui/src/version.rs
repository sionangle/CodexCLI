/// The current Codex CLI version as embedded at compile time.
#[cfg(not(test))]
pub const CODEX_CLI_VERSION: &str = codex_build_info::INTENT_VERSION;

// Keep upstream UI snapshots stable across release-tag version stamping. The
// assembled executable and daemon versions are checked by intent/smoke_package.py.
#[cfg(test)]
pub const CODEX_CLI_VERSION: &str = "0.0.0";
