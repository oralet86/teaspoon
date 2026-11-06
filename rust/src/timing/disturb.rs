//! Detecting timed samples disturbed by other tasks.
//!
//! A timed thread is pinned to one logical CPU, but other tasks can still
//! run on that CPU (preempting it) or on its SMT sibling, which shares the
//! physical core: on the Ryzen 9 7940HS any work on the sibling slows the
//! timed thread by 24–48% (a busy loop) and even a `pause` loop by 14–41%,
//! because the core splits its resources when both threads are active
//! (probe of 2026-11-02).  An idle desktop keeps hundreds of short tasks a
//! second on every CPU, so without root (to disable SMT or move them) the
//! timing code checks each sample instead: [`Watch::disturbed`] says
//! whether the sample's thread was preempted or a task ran on its sibling
//! during it, or the thread page-faulted, from `getrusage` and
//! `/proc/schedstat` (both readable by any user), and the sample is taken
//! again.

use std::cell::RefCell;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};

/// Samples shorter than this (ns) count as disturbed when any task arrived
/// on the sibling during them.
const SHORT_NS: f64 = 200_000.0;
/// Longer samples count as disturbed when tasks ran on the sibling for
/// more than this fraction of the sample.
const SIBLING_SHARE: f64 = 0.005;

/// What [`Watch::now`] reads.
#[derive(Debug, Clone, Copy, Default)]
pub struct Mark {
    /// Context switches of the calling thread (voluntary + involuntary).
    switches: i64,
    /// Task timeslices run on the sibling so far.
    sibling_slices: u64,
    /// Nanoseconds of task time on the sibling so far.
    sibling_ns: u64,
    /// Page faults (minor + major) of the calling thread so far.
    faults: i64,
}

/// Watches the calling thread's CPU and its SMT sibling.
pub struct Watch {
    schedstat: Option<File>,
    sibling: Option<usize>,
    buffer: String,
}

impl Watch {
    /// A watch for the CPU the calling thread runs on now (call it after
    /// pinning).
    fn for_current_cpu() -> Self {
        let sibling = nix::sched::sched_getcpu().ok().and_then(sibling_of);
        Self {
            schedstat: File::open("/proc/schedstat").ok(),
            sibling,
            buffer: String::with_capacity(8192),
        }
    }

    /// The counters now.
    pub fn now(&mut self) -> Mark {
        let (sibling_slices, sibling_ns) = self.sibling_counters().unwrap_or((0, 0));
        let (switches, faults) = thread_usage();
        Mark {
            switches,
            sibling_slices,
            sibling_ns,
            faults,
        }
    }

    /// Whether a sample of `sample_ns` that started at `before` was
    /// disturbed (see the module documentation).
    pub fn disturbed(&mut self, before: Mark, sample_ns: f64) -> bool {
        let after = self.now();
        if after.switches != before.switches || after.faults != before.faults {
            return true;
        }
        let slices = after.sibling_slices.saturating_sub(before.sibling_slices);
        let ran = after.sibling_ns.saturating_sub(before.sibling_ns) as f64;
        if sample_ns < SHORT_NS {
            slices > 0 || ran > 0.0
        } else {
            ran > SIBLING_SHARE * sample_ns
        }
    }

    /// (timeslices, task ns) of the sibling from `/proc/schedstat`:
    /// `cpuN <6 counters> <run ns> <wait ns> <timeslices>`.
    fn sibling_counters(&mut self) -> Option<(u64, u64)> {
        let sibling = self.sibling?;
        let file = self.schedstat.as_mut()?;
        file.seek(SeekFrom::Start(0)).ok()?;
        self.buffer.clear();
        file.read_to_string(&mut self.buffer).ok()?;
        let prefix = format!("cpu{sibling} ");
        let line = self.buffer.lines().find(|line| line.starts_with(&prefix))?;
        let fields: Vec<u64> = line
            .split_whitespace()
            .skip(1)
            .filter_map(|field| field.parse().ok())
            .collect();
        Some((*fields.get(8)?, *fields.get(6)?))
    }
}

/// The other logical CPU of `cpu`'s physical core, if any.
fn sibling_of(cpu: usize) -> Option<usize> {
    super::pin::siblings(cpu)
        .ok()?
        .into_iter()
        .find(|&other| other != cpu)
}

/// (context switches, page faults) of the calling thread so far.
fn thread_usage() -> (i64, i64) {
    use nix::sys::resource::{UsageWho, getrusage};
    getrusage(UsageWho::RUSAGE_THREAD).map_or((0, 0), |usage| {
        (
            usage.voluntary_context_switches() + usage.involuntary_context_switches(),
            usage.minor_page_faults() + usage.major_page_faults(),
        )
    })
}

/// Keeps freed memory in the process (glibc `mallopt`): by default `free`
/// returns memory at the top of a heap to the kernel, and the next
/// allocation then page-faults it back in, inside whatever region is being
/// timed.  Operators that allocate per application (kicks, LK) varied by
/// up to 35% between repeats (E0, 2026-11-02).  Call once at start-up.
pub fn keep_heap() {
    // SAFETY: mallopt only changes allocator parameters; called before any
    // timed work.  Failure (return 0) leaves the defaults, which is safe.
    unsafe {
        libc::mallopt(libc::M_TRIM_THRESHOLD, i32::MAX);
        libc::mallopt(libc::M_TOP_PAD, 64 << 20);
    }
}

/// Busy time before a timed block (ms), see [`spin_up`].
pub const SPIN_UP_MS: u64 = 50;

/// Keeps the calling thread busy for `milliseconds`.  Called before each
/// timed block: after the core has been idle (between blocks, while files
/// are read or written) its first timed work runs slow, because a deep idle
/// state empties the core's caches and the data-fabric clock may have
/// dropped to its lowest level (500 MHz instead of 1600).  Probe of
/// 2026-11-02: after 50 ms idle, an LK batch ran 1.57 times its settled
/// time in the first millisecond, 1.09 times at 4 ms and settled within
/// about 15–30 ms.
pub fn spin_up(milliseconds: u64) {
    let started = std::time::Instant::now();
    let mut state: u64 = 0x9e37_79b9_7f4a_7c15;
    while started.elapsed().as_millis() < u128::from(milliseconds) {
        for _ in 0..1000 {
            state = state
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1);
        }
        std::hint::black_box(state);
    }
}

thread_local! {
    static WATCH: RefCell<Option<(usize, Watch)>> = const { RefCell::new(None) };
    static RETAKEN: RefCell<u64> = const { RefCell::new(0) };
}

/// Runs `f` with the calling thread's watch, created on first use and again
/// whenever the thread is on another CPU than when it was created.
pub fn with_watch<T>(f: impl FnOnce(&mut Watch) -> T) -> T {
    let cpu = nix::sched::sched_getcpu().unwrap_or(usize::MAX);
    WATCH.with(|cell| {
        let mut slot = cell.borrow_mut();
        if slot.as_ref().is_none_or(|(created, _)| *created != cpu) {
            *slot = Some((cpu, Watch::for_current_cpu()));
        }
        f(&mut slot.as_mut().expect("just set").1)
    })
}

/// Counts one retaken sample on the calling thread.
pub fn count_retake() {
    RETAKEN.with(|cell| *cell.borrow_mut() += 1);
}

/// Samples retaken on the calling thread since the last call; resets.
pub fn take_retakes() -> u64 {
    RETAKEN.with(|cell| std::mem::take(&mut *cell.borrow_mut()))
}
