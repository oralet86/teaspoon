//! Provenance recorded in every run's `config.json` (`ideas/rust-port.md`
//! §10.3).

use std::path::Path;
use std::process::Command;

use anyhow::Context;
use serde::Serialize;
use serde_json::{Value, json};

use crate::timing::{machine, tsc};

/// What the binary was built from (embedded by `build.rs`).
#[derive(Debug, Clone, Serialize)]
pub struct Build {
    /// SHA-256 over `src/`, `build.rs` and `Cargo.toml`.
    pub source_sha256: &'static str,
    /// SHA-256 of `Cargo.lock`.
    pub cargo_lock_sha256: &'static str,
    /// `rustc -V`.
    pub rustc: &'static str,
    /// Cargo profile (`release` for every timed run).
    pub profile: &'static str,
    /// Optimisation level.
    pub opt_level: &'static str,
    /// Code-generation flags, including the target CPU.
    pub rustflags: &'static str,
}

/// The build of the running binary.
const BUILD: Build = Build {
    source_sha256: env!("AOS_SOURCE_SHA256"),
    cargo_lock_sha256: env!("AOS_CARGO_LOCK_SHA256"),
    rustc: env!("AOS_RUSTC_VERSION"),
    profile: env!("AOS_PROFILE"),
    opt_level: env!("AOS_OPT_LEVEL"),
    rustflags: env!("AOS_RUSTFLAGS"),
};

fn git(repository: &Path, arguments: &[&str]) -> Option<String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(repository)
        .args(arguments)
        .output()
        .ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).trim().to_string())
}

/// Git commit and dirty flag of the repository at run time.
fn git_state(repository: &Path) -> Value {
    let commit = git(repository, &["rev-parse", "HEAD"]);
    let dirty = git(
        repository,
        &["status", "--porcelain", "--untracked-files=no"],
    )
    .map(|status| !status.is_empty());
    json!({ "commit": commit, "dirty": dirty })
}

/// The host: CPU model and the flags that matter for timing, kernel.
fn host() -> Value {
    let flags = machine::cpu_flags().unwrap_or_default();
    let relevant: Vec<&String> = flags
        .iter()
        .filter(|flag| {
            tsc::CLOCK_FLAGS
                .into_iter()
                .chain(["avx2", "avx512f", "ht"])
                .any(|relevant| flag.as_str() == relevant)
        })
        .collect();
    json!({
        "cpu_model": machine::cpu_model(),
        "cpu_flags": relevant,
        "kernel": machine::kernel(),
        "logical_cpus": std::thread::available_parallelism().map(usize::from).ok(),
    })
}

/// The full provenance block: build, git, host and the random-stream scheme.
pub fn provenance(repository: &Path) -> Value {
    json!({
        "build": BUILD,
        "git": git_state(repository),
        "host": host(),
        "rng": crate::rng::SCHEME,
    })
}

/// Writes `config.json` with provenance and `settings` (atomically).
///
/// # Errors
///
/// If the file cannot be written.
pub fn write_config(directory: &Path, repository: &Path, settings: Value) -> anyhow::Result<()> {
    let payload = json!({ "provenance": provenance(repository), "settings": settings });
    let text = serde_json::to_string_pretty(&payload)? + "\n";
    super::rundir::write_atomic(&directory.join(CONFIG_FILE), text.as_bytes())
}

/// Reads entry `key` of the settings that [`write_config`] stored in
/// `directory`.
///
/// # Errors
///
/// If `config.json` is missing or unreadable, or the entry does not
/// deserialize as `T`.
pub fn read_settings<T: serde::de::DeserializeOwned>(
    directory: &Path,
    key: &str,
) -> anyhow::Result<T> {
    let path = directory.join(CONFIG_FILE);
    let config: Value = super::rundir::read_json(&path)?;
    serde_json::from_value(config["settings"][key].clone())
        .with_context(|| format!("settings.{key} of {}", path.display()))
}

/// The file name of a run's configuration.
pub const CONFIG_FILE: &str = "config.json";
