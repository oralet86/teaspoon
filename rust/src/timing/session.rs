//! A timed session: which core does what, the machine check, pinning and
//! the clock, in the order timing needs them, and the pool of pinned
//! timed workers.
//!
//! Core roles follow [`pin::physical_cores`]: timed work runs on the
//! quietest cores; an untimed main thread that only coordinates workers
//! runs on the last (busiest) core.

use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

use anyhow::{Context, bail};

use super::machine;
use super::pin;
use super::tsc::Clock;

/// Calibration windows of the clock at the start of a session.
pub const CALIBRATION_WINDOWS: usize = 5;

/// Length of each calibration window (ms).
pub const CALIBRATION_WINDOW_MS: u64 = 200;

/// The cores of a timed run.
#[derive(Debug, Clone)]
pub struct CoreRoles {
    /// One logical CPU per physical core, quietest first.
    pub cores: Vec<usize>,
    /// The cores timed work runs on.
    pub timed: Vec<usize>,
    /// The core of the main thread.
    pub main: usize,
}

impl CoreRoles {
    /// `workers` timed workers on the quietest cores and the untimed main
    /// thread on the last one.
    ///
    /// # Errors
    ///
    /// If the topology cannot be read or there are fewer than
    /// `workers + 1` physical cores.
    pub fn workers(workers: usize) -> anyhow::Result<Self> {
        let cores = pin::physical_cores()?;
        if workers + 1 > cores.len() {
            bail!(
                "{workers} timed workers need {} physical cores (one for the main thread), found {}",
                workers + 1,
                cores.len()
            );
        }
        Ok(Self {
            timed: cores[..workers].to_vec(),
            main: *cores.last().expect("at least two cores"),
            cores,
        })
    }

    /// The main thread itself times, on the quietest core.
    ///
    /// # Errors
    ///
    /// If the topology cannot be read.
    pub fn main_times() -> anyhow::Result<Self> {
        let cores = pin::physical_cores()?;
        let main = *cores.first().context("no physical core")?;
        Ok(Self {
            timed: vec![main],
            main,
            cores,
        })
    }
}

/// The cores, the machine's problems and the calibrated clock of a run.
#[derive(Debug, Clone)]
pub struct TimedSession {
    /// Which core does what.
    pub roles: CoreRoles,
    /// What keeps the machine from being locked for timing (empty when it
    /// is; non-empty only with `allow_unlocked`).
    pub problems: Vec<String>,
    /// The clock, calibrated on the main thread's core.
    pub clock: Clock,
}

impl TimedSession {
    /// Checks the machine, pins the calling (main) thread to its core and
    /// calibrates the clock there.
    ///
    /// # Errors
    ///
    /// If the machine is not locked for timing and `allow_unlocked` is not
    /// set, or pinning or calibration fails.
    pub fn open(roles: CoreRoles, allow_unlocked: bool) -> anyhow::Result<Self> {
        let problems = machine::require_locked(&roles.cores, allow_unlocked)?;
        pin::pin_current_thread(roles.main)?;
        let clock = Clock::calibrate(CALIBRATION_WINDOWS, CALIBRATION_WINDOW_MS)?;
        Ok(Self {
            roles,
            problems,
            clock,
        })
    }

    /// Runs `work` on every item of `items` on the timed workers, one
    /// thread pinned to each timed core.  Each worker first builds its own
    /// state with `start(cpu)` on its pinned thread (counters opened there
    /// count that thread only), then takes items in order until none is
    /// left or a worker failed.
    ///
    /// # Errors
    ///
    /// The first error of any worker.
    pub fn run_pinned<T: Sync, S>(
        &self,
        items: &[T],
        start: impl Fn(usize) -> anyhow::Result<S> + Sync,
        work: impl Fn(&mut S, usize, &T) -> anyhow::Result<()> + Sync,
    ) -> anyhow::Result<()> {
        let next = AtomicUsize::new(0);
        let errors: Mutex<Vec<anyhow::Error>> = Mutex::new(Vec::new());
        std::thread::scope(|scope| {
            for &cpu in &self.roles.timed {
                let (next, errors, start, work) = (&next, &errors, &start, &work);
                scope.spawn(move || {
                    let result = (|| -> anyhow::Result<()> {
                        pin::pin_current_thread(cpu)?;
                        let mut state = start(cpu)?;
                        while errors.lock().expect("lock").is_empty() {
                            let Some(item) = items.get(next.fetch_add(1, Ordering::Relaxed)) else {
                                break;
                            };
                            work(&mut state, cpu, item)?;
                        }
                        Ok(())
                    })();
                    if let Err(error) = result {
                        errors.lock().expect("lock").push(error);
                    }
                });
            }
        });
        match errors
            .into_inner()
            .expect("workers finished")
            .into_iter()
            .next()
        {
            Some(error) => Err(error),
            None => Ok(()),
        }
    }
}
