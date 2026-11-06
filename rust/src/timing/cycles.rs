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
        let until = super::monotonic_raw_ns() + ms * 1_000_000;
        while super::monotonic_raw_ns() < until {
            std::hint::spin_loop();
        }
        self.read()?
            .khz_since(&before)
            .context("the reference loop did not run")
    }
}

/// Largest allowed change of the measured core frequency within a timed
/// block, as a fraction.
pub const FREQUENCY_DRIFT: f64 = 0.01;

/// Length of the busy loop that measures a thread's reference frequency
/// (ms).
const REFERENCE_MS: u64 = 200;

/// The calling thread's core-cycle counters and the reference frequency
/// measured with them, for checking that timed blocks ran at it.
pub struct FrequencyWatch {
    counters: Option<ThreadCounters>,
    /// The frequency of a [`REFERENCE_MS`] busy loop on this thread (kHz).
    pub reference_khz: Option<f64>,
}

impl FrequencyWatch {
    /// Opens the counters on the calling thread and measures its reference
    /// frequency.  Without counters the watch reports nothing, which only
    /// `allow_unlocked` permits (their absence is then a recorded machine
    /// problem).
    ///
    /// # Errors
    ///
    /// If the counters cannot be opened and `allow_unlocked` is not set, or
    /// a reading fails.
    pub fn on_this_thread(allow_unlocked: bool) -> anyhow::Result<Self> {
        let mut counters = match ThreadCounters::open() {
            Ok(counters) => Some(counters),
            Err(error) if allow_unlocked => {
                log::warn!("no core-cycle counter: {error:#}");
                None
            }
            Err(error) => return Err(error),
        };
        let reference_khz = match counters.as_mut() {
            Some(counters) => Some(counters.reference_khz(REFERENCE_MS)?),
            None => None,
        };
        Ok(Self {
            counters,
            reference_khz,
        })
    }

    /// The counters now (`None` without counters).
    ///
    /// # Errors
    ///
    /// If a counter cannot be read.
    pub fn read(&mut self) -> anyhow::Result<Option<Reading>> {
        self.counters.as_mut().map(ThreadCounters::read).transpose()
    }

    /// The average frequency since `before` (kHz), when both are known.
    ///
    /// # Errors
    ///
    /// If a counter cannot be read.
    pub fn khz_since(&mut self, before: Option<Reading>) -> anyhow::Result<Option<f64>> {
        Ok(match (self.read()?, before) {
            (Some(after), Some(before)) => after.khz_since(&before),
            _ => None,
        })
    }

    /// Whether the block since `before` ran more than [`FREQUENCY_DRIFT`]
    /// away from the reference frequency.
    ///
    /// # Errors
    ///
    /// If a counter cannot be read.
    pub fn drifted_since(&mut self, before: Option<Reading>) -> anyhow::Result<bool> {
        let average = self.khz_since(before)?;
        Ok(drifted(self.reference_khz, average, FREQUENCY_DRIFT))
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
