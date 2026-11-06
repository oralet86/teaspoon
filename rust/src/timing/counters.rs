//! Instructions and core cycles of a timed region, from the CPU's
//! performance counters (`perf-event2`), read in user space.
//!
//! Each timed region is also counted: user-space instructions retired and
//! user-space core cycles of the calling thread.  The instruction count of
//! an identical region is the same in every repeat whatever the rest of the
//! machine does (probe of 2026-11-02: identical over 80 rounds, unchanged
//! while another core streamed 256 MB of memory), so it is a
//! machine-independent second measure of an operator's work.  It is not a
//! substitute for time: time per instruction differs fourfold within one
//! operator between states (short branchy reversals against long simple
//! ones), and instruction counts ordered close operator pairs as time does
//! in only about three cases in four.  Cycles follow time within about 1%
//! at a locked clock but count only while the thread runs in user space,
//! so time well above cycles means the thread was preempted or interrupted
//! during the region.
//!
//! The counters are read with `rdpmc` from a page the kernel maps for each
//! counter, without a system call: a system call between samples would
//! disturb the caches just before a region of a few microseconds.  The
//! counters of a thread are opened on first use; where they cannot be
//! (no hardware counters, `perf_event_paranoid` above 2, a virtual machine
//! without a PMU) a warning is logged once and regions have no counts.

use std::cell::RefCell;

use perf_event::events::Hardware;
use perf_event::{Builder, Sampler};

use super::tsc;

/// User-space instructions and cycles of one region.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Counts {
    /// Instructions retired.
    pub instructions: f64,
    /// Core cycles.
    pub cycles: f64,
}

impl Counts {
    /// Both counts multiplied by `factor`.
    #[must_use]
    pub fn scaled(self, factor: f64) -> Self {
        Self {
            instructions: self.instructions * factor,
            cycles: self.cycles * factor,
        }
    }

    /// Both counts minus `other`'s.
    #[must_use]
    pub fn minus(self, other: Self) -> Self {
        Self {
            instructions: self.instructions - other.instructions,
            cycles: self.cycles - other.cycles,
        }
    }

    /// The mean of two counts (for the median of an even number of samples).
    #[must_use]
    pub fn mean_with(self, other: Self) -> Self {
        Self {
            instructions: (self.instructions + other.instructions) / 2.0,
            cycles: (self.cycles + other.cycles) / 2.0,
        }
    }
}

/// Both counters at one point of the calling thread.
#[derive(Debug, Clone, Copy)]
pub struct Reading {
    instructions: u64,
    cycles: u64,
}

/// Empty regions timed to measure the counting overhead.
const OVERHEAD_REGIONS: usize = 101;

/// The counting overhead of the process (see [`Counters::open`]).
static OVERHEAD: std::sync::OnceLock<Counts> = std::sync::OnceLock::new();

struct Counters {
    instructions: Sampler,
    cycles: Sampler,
    /// Counts of an empty region (the `tsc` reads and the tail and head of
    /// the counter reads), removed from every region; the same for every
    /// thread of the process.
    overhead: Counts,
}

impl Counters {
    fn open() -> std::io::Result<Self> {
        let open = |event: Hardware| -> std::io::Result<Sampler> {
            // Defaults: the calling thread on any CPU, user space only.
            // Pinned, so that the counter is never multiplexed off the PMU
            // (a multiplexed counter cannot be read with rdpmc).
            let mut counter = Builder::new(event).pinned(true).build()?;
            counter.enable()?;
            counter.sampled(0)
        };
        let mut counters = Self {
            instructions: open(Hardware::INSTRUCTIONS)?,
            cycles: open(Hardware::CPU_CYCLES)?,
            overhead: Counts::default(),
        };
        // Measured once per process, on the first thread that counts, so
        // that every thread removes the same overhead: estimated per
        // thread, it differed by 53 instructions between two threads (E0
        // 2026-11-02), a spurious 12% change of a cheap operator's count.
        if let Some(&overhead) = OVERHEAD.get() {
            counters.overhead = overhead;
            return Ok(counters);
        }
        let mut instructions = Vec::with_capacity(OVERHEAD_REGIONS);
        let mut cycles = Vec::with_capacity(OVERHEAD_REGIONS);
        for _ in 0..OVERHEAD_REGIONS {
            let before = counters.read().ok_or_else(unreadable)?;
            std::hint::black_box((tsc::start(), tsc::stop()));
            let after = counters.read().ok_or_else(unreadable)?;
            instructions.push(after.instructions.wrapping_sub(before.instructions) as f64);
            cycles.push(after.cycles.wrapping_sub(before.cycles) as f64);
        }
        instructions.sort_by(f64::total_cmp);
        cycles.sort_by(f64::total_cmp);
        counters.overhead = *OVERHEAD.get_or_init(|| Counts {
            instructions: instructions[0],
            cycles: cycles[OVERHEAD_REGIONS / 2],
        });
        Ok(counters)
    }

    fn read(&self) -> Option<Reading> {
        Some(Reading {
            instructions: self.instructions.read_user().count()?,
            cycles: self.cycles.read_user().count()?,
        })
    }
}

fn unreadable() -> std::io::Error {
    std::io::Error::other("the counters cannot be read with rdpmc")
}

thread_local! {
    /// The calling thread's counters: not yet opened, opened, or failed.
    static COUNTERS: RefCell<Option<Option<Counters>>> = const { RefCell::new(None) };
}

/// Runs `f` with the calling thread's counters, opened on first use; `None`
/// (after one logged warning per thread) when they cannot be opened.
fn with_counters<T>(f: impl FnOnce(&Counters) -> Option<T>) -> Option<T> {
    COUNTERS.with(|cell| {
        let mut slot = cell.borrow_mut();
        let counters = slot.get_or_insert_with(|| match Counters::open() {
            Ok(counters) => Some(counters),
            Err(error) => {
                log::warn!("performance counters unavailable, regions are not counted: {error}");
                None
            }
        });
        counters.as_ref().and_then(f)
    })
}

/// Both counters of the calling thread now (call just before `tsc::start`).
#[must_use]
pub fn read() -> Option<Reading> {
    with_counters(Counters::read)
}

/// The counts of the region between `before` and `after` (read just after
/// `tsc::stop`), the counting overhead removed.
#[must_use]
pub fn between(before: Option<Reading>, after: Option<Reading>) -> Option<Counts> {
    let (before, after) = (before?, after?);
    with_counters(|counters| {
        Some(
            Counts {
                instructions: after.instructions.wrapping_sub(before.instructions) as f64,
                cycles: after.cycles.wrapping_sub(before.cycles) as f64,
            }
            .minus(counters.overhead),
        )
    })
}

/// Whether the calling thread's counters can be opened and read (for the
/// machine report).
///
/// # Errors
///
/// The reason they cannot.
pub fn check() -> std::io::Result<()> {
    Counters::open().map(drop)
}
