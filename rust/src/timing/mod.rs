//! The clock, core pinning and machine-state checks (`ideas/rust-port.md`
//! §7).  The only `unsafe` code of the crate lives here.

pub mod counters;
pub mod cycles;
pub mod disturb;
pub mod machine;
pub mod pin;
pub mod session;
pub mod tsc;

/// Nanoseconds of `CLOCK_MONOTONIC_RAW`.
pub fn monotonic_raw_ns() -> u64 {
    use nix::time::{ClockId, clock_gettime};
    let time = clock_gettime(ClockId::CLOCK_MONOTONIC_RAW)
        .expect("clock_gettime(CLOCK_MONOTONIC_RAW) failed (Linux 2.6.28 or later has it)");
    time.tv_sec() as u64 * 1_000_000_000 + time.tv_nsec() as u64
}
