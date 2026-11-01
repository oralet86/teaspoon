//! The distance gate of `ideas/rust-port.md` §11.1 against the Python
//! reference.  Needs the export of `rust/tools/export_reference.py`; run
//! with `AOS_REFERENCE=<dir> cargo test --release -- --ignored reference`.
//! Without the variable the test fails rather than passing vacuously.

use std::path::{Path, PathBuf};

use aos::instance::alpha::CACHE_DIRECTORY;
use aos::instance::reference::{reference_files, verify};

#[test]
#[ignore = "needs AOS_REFERENCE (rust/tools/export_reference.py)"]
fn reference_matrices_and_neighbours_match_python() {
    let directory = PathBuf::from(
        std::env::var("AOS_REFERENCE")
            .expect("set AOS_REFERENCE to the exported reference directory"),
    );
    let repository = Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    let cache = repository.join(CACHE_DIRECTORY);
    let failures: Vec<String> = reference_files(&directory)
        .expect("reference directory is readable")
        .iter()
        .map(|file| verify(file, &repository, &cache).expect("instance loads"))
        .filter(|report| !report.passed())
        .map(|report| format!("{report:?}"))
        .collect();
    assert!(failures.is_empty(), "mismatches: {failures:#?}");
}
