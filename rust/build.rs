//! Embeds what the binary was built from into provenance: a digest of the
//! Rust sources, the `Cargo.lock` hash, the compiler version, the profile
//! and the code-generation flags (`ideas/rust-port.md` §10.3).

use std::path::{Path, PathBuf};
use std::process::Command;

use sha2::{Digest, Sha256};

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn files(directory: &Path, out: &mut Vec<PathBuf>) {
    let entries = std::fs::read_dir(directory).expect("source directory is readable");
    for entry in entries {
        let path = entry.expect("directory entry is readable").path();
        if path.is_dir() {
            files(&path, out);
        } else {
            out.push(path);
        }
    }
}

fn main() {
    let mut sources = Vec::new();
    for root in ["src", "build.rs", "Cargo.toml"] {
        let path = PathBuf::from(root);
        if path.is_dir() {
            files(&path, &mut sources);
        } else {
            sources.push(path);
        }
    }
    sources.sort();
    let mut hasher = Sha256::new();
    for path in &sources {
        hasher.update(path.to_string_lossy().as_bytes());
        hasher.update([0]);
        hasher.update(std::fs::read(path).expect("source file is readable"));
        hasher.update([0]);
    }
    println!(
        "cargo:rustc-env=AOS_SOURCE_SHA256={}",
        hex(&hasher.finalize())
    );
    let lock = std::fs::read("Cargo.lock").expect("Cargo.lock exists");
    println!(
        "cargo:rustc-env=AOS_CARGO_LOCK_SHA256={}",
        hex(&Sha256::digest(&lock))
    );
    let rustc = std::env::var("RUSTC").unwrap_or_else(|_| "rustc".to_string());
    let version = Command::new(rustc)
        .arg("-V")
        .output()
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
        .unwrap_or_else(|_| "unknown".to_string());
    println!("cargo:rustc-env=AOS_RUSTC_VERSION={version}");
    let profile = std::env::var("PROFILE").unwrap_or_default();
    println!("cargo:rustc-env=AOS_PROFILE={profile}");
    let flags = std::env::var("CARGO_ENCODED_RUSTFLAGS")
        .unwrap_or_default()
        .replace('\u{1f}', " ");
    println!("cargo:rustc-env=AOS_RUSTFLAGS={flags}");
    let opt_level = std::env::var("OPT_LEVEL").unwrap_or_default();
    println!("cargo:rustc-env=AOS_OPT_LEVEL={opt_level}");
    println!("cargo:rerun-if-changed=src");
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=Cargo.toml");
    println!("cargo:rerun-if-changed=Cargo.lock");
}
