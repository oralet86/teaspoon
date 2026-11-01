//! Operator-selection experiments for the symmetric TSP: the Rust port of
//! the Python package `aos/` (specification: `ideas/rust-port.md`).
//!
//! The library holds everything that produces data: instances and
//! distances ([`instance`]), seeded random streams ([`rng`]), the clock and
//! machine checks ([`timing`]) and run directories ([`io`]).  The `aos`
//! binary exposes one subcommand per experiment.  Figures stay in Python
//! (`aos/analysis/`), which reads the run directories as files.

pub mod experiments;
pub mod instance;
pub mod io;
pub mod ops;
pub mod rng;
pub mod timing;
pub mod tour;

/// A city index, `0..n`.
pub type City = u32;
