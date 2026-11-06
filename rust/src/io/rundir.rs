//! Run directories: `runs/<experiment>-rs/<timestamp>[-unlocked]/`.

use std::path::{Path, PathBuf};

use anyhow::{Context, bail};

/// Creates and returns a fresh run directory under `root`.  Runs on an
/// unlocked machine get the suffix `-unlocked`, so their timings cannot be
/// mistaken for locked ones.
///
/// # Errors
///
/// If the directory exists already or cannot be created.
pub fn new_run_directory(root: &Path, experiment: &str, unlocked: bool) -> anyhow::Result<PathBuf> {
    let stamp = timestamp();
    let suffix = if unlocked { "-unlocked" } else { "" };
    let directory = root
        .join(format!("{experiment}-rs"))
        .join(format!("{stamp}{suffix}"));
    if directory.exists() {
        bail!("{} exists already", directory.display());
    }
    std::fs::create_dir_all(&directory)
        .with_context(|| format!("creating {}", directory.display()))?;
    Ok(directory)
}

/// Local time as `YYYYmmdd-HHMMSS`, like the Python run directories.
pub fn timestamp() -> String {
    jiff::Zoned::now().strftime("%Y%m%d-%H%M%S").to_string()
}

/// Writes `contents` to `path` atomically: a temporary file, then rename.
///
/// # Errors
///
/// If either step fails.
pub fn write_atomic(path: &Path, contents: &[u8]) -> anyhow::Result<()> {
    let temporary = temporary_path(path);
    std::fs::write(&temporary, contents)
        .with_context(|| format!("writing {}", temporary.display()))?;
    std::fs::rename(&temporary, path).with_context(|| format!("renaming to {}", path.display()))?;
    Ok(())
}

/// Reads a JSON file as `T`.
///
/// # Errors
///
/// If the file cannot be read or parsed as `T`.
pub fn read_json<T: serde::de::DeserializeOwned>(path: &Path) -> anyhow::Result<T> {
    let text =
        std::fs::read_to_string(path).with_context(|| format!("reading {}", path.display()))?;
    serde_json::from_str(&text).with_context(|| format!("parsing {}", path.display()))
}

/// `path` with `.tmp` appended to its file name.
pub fn temporary_path(path: &Path) -> PathBuf {
    let mut name = path.file_name().unwrap_or_default().to_os_string();
    name.push(".tmp");
    path.with_file_name(name)
}
