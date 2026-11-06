//! The invariant time-stamp counter as the clock of timed runs.
//!
//! A timed region is read as `lfence; rdtsc; lfence` at the start and
//! `rdtscp; lfence` at the end, so earlier instructions finish before the
//! first read and the region's instructions before the second.  Ticks are
//! converted to nanoseconds with a frequency calibrated at the start of
//! every run against `CLOCK_MONOTONIC_RAW`.

use std::arch::x86_64::{__rdtscp, _mm_lfence, _rdtsc};

use anyhow::bail;
use serde::Serialize;

/// Reads the counter at the start of a timed region.
#[inline(always)]
pub fn start() -> u64 {
    // SAFETY: lfence and rdtsc have no memory effects and are available
    // on every x86-64 CPU; `require_invariant_tsc` checked the counter.
    unsafe {
        _mm_lfence();
        let ticks = _rdtsc();
        _mm_lfence();
        ticks
    }
}

/// Reads the counter at the end of a timed region.
#[inline(always)]
pub fn stop() -> u64 {
    let mut processor = 0u32;
    // SAFETY: rdtscp writes only `processor`, a valid local; the CPU flag
    // `rdtscp` is checked by `require_invariant_tsc`.
    unsafe {
        let ticks = __rdtscp(&mut processor);
        _mm_lfence();
        ticks
    }
}

/// Nanoseconds of `CLOCK_MONOTONIC_RAW`.
pub fn monotonic_raw_ns() -> u64 {
    use nix::time::{ClockId, clock_gettime};
    let time = clock_gettime(ClockId::CLOCK_MONOTONIC_RAW)
        .expect("clock_gettime(CLOCK_MONOTONIC_RAW) failed (Linux 2.6.28 or later has it)");
    time.tv_sec() as u64 * 1_000_000_000 + time.tv_nsec() as u64
}

/// Stops unless the CPU has an invariant counter (`constant_tsc`,
/// `nonstop_tsc`) and `rdtscp`.
///
/// # Errors
///
/// Naming the missing flags, or if `/proc/cpuinfo` cannot be read.
pub fn require_invariant_tsc() -> anyhow::Result<()> {
    let flags = super::machine::cpu_flags()?;
    let missing: Vec<&str> = ["constant_tsc", "nonstop_tsc", "rdtscp"]
        .into_iter()
        .filter(|flag| !flags.iter().any(|present| present == flag))
        .collect();
    if !missing.is_empty() {
        bail!("CPU lacks {missing:?}; the TSC cannot serve as the clock");
    }
    Ok(())
}

fn gcd(a: u64, b: u64) -> u64 {
    if b == 0 { a } else { gcd(b, a % b) }
}

/// Empty regions (and consecutive reads) sampled to measure the overhead
/// and the step of the counter.
const EMPTY_REGIONS: usize = 100_000;

/// An empty timed region longer than this many times the median one was
/// interrupted; such regions are left out of the overhead.
pub const OUTLIER_FACTOR: u64 = 4;

/// The calibrated clock of one run.
#[derive(Debug, Clone, Copy, Serialize)]
pub struct Clock {
    /// Counter ticks per nanosecond (median of the calibration windows).
    pub ticks_per_ns: f64,
    /// Relative spread (max - min) / median of the window estimates.
    pub calibration_spread: f64,
    /// Mean ticks of an empty timed region (`start` then `stop`);
    /// subtracted from every timed region.  The mean, not the median: the
    /// counter advances in steps (`step_ticks`), so an empty region reads
    /// one of a few values and the median is off by up to a step, while
    /// rates need the mean time.  Regions longer than
    /// [`OUTLIER_FACTOR`] times the median are left out: they were
    /// interrupted, and a few interrupts among the samples moved the mean
    /// by several ticks between calibrations.
    pub overhead_ticks: f64,
    /// Empty regions left out of `overhead_ticks` as interrupted.
    pub overhead_outliers: usize,
    /// Median ticks of an empty timed region (recorded for comparison).
    pub overhead_median_ticks: u64,
    /// The counter's step: the greatest common divisor of the gaps between
    /// consecutive fenced reads (40 ticks = 10 ns on the Zen 4 machine; 1
    /// on a counter that moves every tick).
    pub step_ticks: u64,
}

impl Clock {
    /// Calibrates the counter against `CLOCK_MONOTONIC_RAW` over `windows`
    /// windows of `window_ms` each, and measures the read overhead.
    ///
    /// # Errors
    ///
    /// If the counter is not invariant or the windows disagree by more
    /// than 0.1%.
    pub fn calibrate(windows: usize, window_ms: u64) -> anyhow::Result<Self> {
        require_invariant_tsc()?;
        let mut estimates = Vec::with_capacity(windows);
        for _ in 0..windows {
            let wall_start = monotonic_raw_ns();
            let tick_start = start();
            while monotonic_raw_ns() - wall_start < window_ms * 1_000_000 {
                std::hint::spin_loop();
            }
            let tick_end = stop();
            let wall_end = monotonic_raw_ns();
            estimates.push((tick_end - tick_start) as f64 / (wall_end - wall_start) as f64);
        }
        estimates.sort_by(f64::total_cmp);
        let median = estimates[estimates.len() / 2];
        let spread = (estimates[estimates.len() - 1] - estimates[0]) / median;
        if spread > 1e-3 {
            bail!("TSC calibration windows disagree by {:.3}%", spread * 100.0);
        }
        let mut gaps: Vec<u64> = (0..EMPTY_REGIONS)
            .map(|_| {
                let begin = start();
                stop() - begin
            })
            .collect();
        gaps.sort_unstable();
        let overhead_median = gaps[gaps.len() / 2];
        let kept: Vec<u64> = gaps
            .iter()
            .copied()
            .filter(|&gap| gap <= OUTLIER_FACTOR * overhead_median)
            .collect();
        // The step is the greatest common divisor of the gaps between
        // consecutive fenced reads, not their minimum: a fenced read can
        // take longer than one step (80-120 ticks per gap at 4 GHz on the
        // Zen 4 machine, whose counter moves in 40-tick steps).
        let mut step = 0;
        let mut previous = start();
        for _ in 0..EMPTY_REGIONS {
            let now = start();
            step = gcd(step, now - previous);
            previous = now;
        }
        Ok(Self {
            ticks_per_ns: median,
            calibration_spread: spread,
            overhead_ticks: kept.iter().sum::<u64>() as f64 / kept.len() as f64,
            overhead_outliers: gaps.len() - kept.len(),
            overhead_median_ticks: overhead_median,
            step_ticks: step,
        })
    }

    /// The overhead subtracted from every timed region, in nanoseconds.
    pub fn overhead_ns(&self) -> f64 {
        self.overhead_ticks / self.ticks_per_ns
    }

    /// Converts a tick count to nanoseconds.
    #[inline]
    pub fn ns(&self, ticks: u64) -> f64 {
        ticks as f64 / self.ticks_per_ns
    }

    /// Nanoseconds of a timed region, overhead subtracted (may be negative
    /// for regions shorter than the overhead's spread).
    #[inline]
    pub fn region_ns(&self, begin: u64, end: u64) -> f64 {
        (end as f64 - begin as f64 - self.overhead_ticks) / self.ticks_per_ns
    }
}
