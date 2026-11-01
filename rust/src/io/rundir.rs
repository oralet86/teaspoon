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
    // SAFETY: `time(NULL)` has no preconditions; `localtime_r` writes into
    // the valid `tm` it is given.
    let mut tm: libc::tm = unsafe { std::mem::zeroed() };
    unsafe {
        let now = libc::time(std::ptr::null_mut());
        libc::localtime_r(&now, &mut tm);
    }
    format!(
        "{:04}{:02}{:02}-{:02}{:02}{:02}",
        tm.tm_year + 1900,
        tm.tm_mon + 1,
        tm.tm_mday,
        tm.tm_hour,
        tm.tm_min,
        tm.tm_sec
    )
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

/// `path` with `.tmp` appended to its file name.
pub fn temporary_path(path: &Path) -> PathBuf {
    let mut name = path.file_name().unwrap_or_default().to_os_string();
    name.push(".tmp");
    path.with_file_name(name)
}
