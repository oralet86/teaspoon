//! Core cycles of the calling thread from the kernel's performance
//! counters (`perf_event_open`, through `perf-event2`), for the average
//! core frequency over a stretch of timed work.
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

use anyhow::Context;
use perf_event::events::{Hardware, Software};
use perf_event::{Builder, Counter};

/// Opens one counting event of the calling thread on any CPU, user space
/// only (`perf-event2`'s defaults), and enables it.
fn open_event(event: impl perf_event::events::Event) -> anyhow::Result<Counter> {
    let mut counter = Builder::new(event)
        .build()
        .context("opening a performance counter (check /proc/sys/kernel/perf_event_paranoid)")?;
    counter.enable().context("enabling a performance counter")?;
    Ok(counter)
}

/// The two counters of one thread.  Must be read on the thread that
/// opened them (they count that thread only).
pub struct ThreadCounters {
    cycles: Counter,
    task_clock: Counter,
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
            cycles: open_event(Hardware::CPU_CYCLES)?,
            task_clock: open_event(Software::TASK_CLOCK)?,
        })
    }

    /// Reads both counters.
    ///
    /// # Errors
    ///
    /// If a counter cannot be read.
    pub fn read(&mut self) -> anyhow::Result<Reading> {
        Ok(Reading {
            cycles: self.cycles.read().context("reading the cycle counter")?,
            task_ns: self.task_clock.read().context("reading the task clock")?,
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
