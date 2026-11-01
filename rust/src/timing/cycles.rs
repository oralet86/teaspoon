//! Core cycles of the calling thread from the kernel's performance
//! counters (`perf_event_open`), for the average core frequency over a
//! stretch of timed work.
//!
//! The time-stamp counter measures wall time at a fixed rate whatever the
//! core frequency, so a frequency change during a state goes unnoticed in
//! the timings themselves.  Two counters of the calling thread are opened:
//! user-space core cycles and the task clock (time the thread ran).  Their
//! ratio over a stretch of work is its average core frequency.
//!
//! Only user-space cycles can be counted without root
//! (`perf_event_paranoid` = 2), while the task clock also includes time
//! spent in the kernel on the thread's behalf, so the ratio is a slight
//! *lower* bound of the true frequency; for the timing loops, which make
//! almost no system calls, the difference is small.  A stretch whose
//! average falls below the reference by more than the tolerance is flagged
//! as suspect, never silently accepted.

use std::fs::File;
use std::io::Read;
use std::os::fd::FromRawFd;

use anyhow::{Context, bail};

/// `PERF_TYPE_HARDWARE` / `PERF_COUNT_HW_CPU_CYCLES`.
const TYPE_HARDWARE: u32 = 0;
const HW_CPU_CYCLES: u64 = 0;
/// `PERF_TYPE_SOFTWARE` / `PERF_COUNT_SW_TASK_CLOCK`.
const TYPE_SOFTWARE: u32 = 1;
const SW_TASK_CLOCK: u64 = 1;
/// `perf_event_attr` flag bits: `exclude_kernel` and `exclude_hv`.
const EXCLUDE_KERNEL: u64 = 1 << 5;
const EXCLUDE_HV: u64 = 1 << 6;

/// The first 64 bytes of `struct perf_event_attr` (`PERF_ATTR_SIZE_VER0`),
/// which is all a counting event needs; the kernel accepts this size.
#[repr(C)]
#[derive(Default)]
struct Attr {
    kind: u32,
    size: u32,
    config: u64,
    sample_period: u64,
    sample_type: u64,
    read_format: u64,
    flags: u64,
    wakeup_events: u32,
    bp_type: u32,
    config1: u64,
}

/// Opens one counting event of the calling thread, on any CPU.
fn open_event(kind: u32, config: u64) -> anyhow::Result<File> {
    let attr = Attr {
        kind,
        size: std::mem::size_of::<Attr>() as u32,
        config,
        flags: EXCLUDE_KERNEL | EXCLUDE_HV,
        ..Attr::default()
    };
    // SAFETY: `attr` is a valid, initialised perf_event_attr prefix whose
    // `size` field states its length; pid 0 / cpu -1 is the calling thread
    // on any CPU, group -1 and flags 0 open a standalone counter.
    let fd = unsafe {
        libc::syscall(
            libc::SYS_perf_event_open,
            &attr as *const Attr,
            0 as libc::pid_t,
            -1 as libc::c_int,
            -1 as libc::c_int,
            0 as libc::c_ulong,
        )
    };
    if fd < 0 {
        bail!(
            "perf_event_open(type {kind}, config {config}) failed: {} (check /proc/sys/kernel/perf_event_paranoid)",
            std::io::Error::last_os_error()
        );
    }
    // SAFETY: the syscall returned a new file descriptor that nothing else
    // owns; the File takes ownership and closes it.
    Ok(unsafe { File::from_raw_fd(fd as libc::c_int) })
}

fn read_counter(file: &mut File) -> anyhow::Result<u64> {
    let mut bytes = [0u8; 8];
    file.read_exact(&mut bytes)
        .context("reading a performance counter")?;
    Ok(u64::from_ne_bytes(bytes))
}

/// The two counters of one thread.  Must be read on the thread that
/// opened them (they count that thread only).
pub struct ThreadCounters {
    cycles: File,
    task_clock: File,
}

/// One reading of [`ThreadCounters`].
#[derive(Debug, Clone, Copy)]
pub struct Reading {
    /// User-space core cycles so far.
    pub cycles: u64,
    /// Nanoseconds the thread ran so far.
    pub task_ns: u64,
}

impl Reading {
    /// Average core frequency (kHz) between `earlier` and this reading, or
    /// `None` when the thread did not run in between.
    pub fn khz_since(&self, earlier: &Reading) -> Option<f64> {
        let ns = self.task_ns.checked_sub(earlier.task_ns)?;
        let cycles = self.cycles.checked_sub(earlier.cycles)?;
        (ns > 0).then(|| cycles as f64 / ns as f64 * 1e6)
    }
}

impl ThreadCounters {
    /// Opens the counters for the calling thread.
    ///
    /// # Errors
    ///
    /// If the kernel refuses either counter (no hardware counters, a
    /// virtual machine, or `perf_event_paranoid` above 2).
    pub fn open() -> anyhow::Result<Self> {
        Ok(Self {
            cycles: open_event(TYPE_HARDWARE, HW_CPU_CYCLES)?,
            task_clock: open_event(TYPE_SOFTWARE, SW_TASK_CLOCK)?,
        })
    }

    /// Reads both counters.
    ///
    /// # Errors
    ///
    /// If a counter cannot be read.
    pub fn read(&mut self) -> anyhow::Result<Reading> {
        Ok(Reading {
            cycles: read_counter(&mut self.cycles)?,
            task_ns: read_counter(&mut self.task_clock)?,
        })
    }

    /// The average core frequency (kHz) of a busy loop of `ms`
    /// milliseconds: the reference that later stretches are compared with.
    ///
    /// # Errors
    ///
    /// If a counter cannot be read or the loop did not run.
    pub fn reference_khz(&mut self, ms: u64) -> anyhow::Result<f64> {
        let before = self.read()?;
        let until = super::tsc::monotonic_raw_ns() + ms * 1_000_000;
        while super::tsc::monotonic_raw_ns() < until {
            std::hint::spin_loop();
        }
        self.read()?
            .khz_since(&before)
            .context("the reference loop did not run")
    }
}

/// Whether `khz` is more than `tolerance` (a fraction) away from
/// `reference`; unknown values count as no drift.
pub fn drifted(reference: Option<f64>, khz: Option<f64>, tolerance: f64) -> bool {
    match (reference, khz) {
        (Some(reference), Some(khz)) => (khz - reference).abs() / reference > tolerance,
        _ => false,
    }
}
