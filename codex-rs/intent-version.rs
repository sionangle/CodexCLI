// Shared by CLI build information and daemon diagnostics without new crate dependencies.
pub const INTENT_VERSION: &str = concat!(env!("CARGO_PKG_VERSION"), "+intent.1");
