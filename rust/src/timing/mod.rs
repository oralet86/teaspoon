//! The clock, core pinning and machine-state checks (`ideas/rust-port.md`
//! §7).  The only `unsafe` code of the crate lives here.

pub mod counters;
pub mod cycles;
pub mod disturb;
pub mod machine;
pub mod pin;
pub mod tsc;
