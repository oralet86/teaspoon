//! Measurement A: exact marginal rates of every operator in every state
//! (`aos/marginal.py`, `aos/e1_marginal.py`; `ideas/rust-port.md` §8.3).
//!
//! Three stages over the states of a states run (Rust-built or the Python
//! E1 states):
//!
//! 1. **gains** (untimed, every CPU): the exact local-optimality flags of
//!    each state, its anchors (all cities, or a seeded sample sized by a
//!    pilot so that `min_rounds` timed rounds fit `state_seconds`), and
//!    every application's gain, counted work and resulting tour hash.
//!    Deterministic operators run once per anchor, the others `repeats`
//!    times, each from its own stream.
//! 2. **times** (pinned timed workers, nothing else running): rounds over
//!    every operator in a shuffled order; each operator runs over every
//!    anchor (shuffled per round) back to back, as a search runs it during
//!    a slice, never interleaved with other operators.  Each (operator,
//!    round) has up to three timed passes, in a random order: per call
//!    (one region per application: the spread over anchors), batched (one
//!    region around every application, each restored to the state), and,
//!    when some application improves, a restore batch (one region around
//!    replaying each improving application's recorded changes and
//!    restoring them: about two restores each, without the search).  The operator's time is the
//!    batched time per application with the restore removed.  Rounds stop
//!    when the deterministic operators' batched round means are within
//!    `precision` (95% t-interval), the state budget is spent, or
//!    `max_rounds` is reached.  Every replay must reproduce its pass-1
//!    gain (and, per call, its tour hash), else the run stops.  The core
//!    frequency over each state is checked with the core-cycle counter.
//! 3. **summarise** (untimed, every CPU): per (state, operator) the rate
//!    (mean gain over batched mean time) and its bootstrap interval (on the
//!    per-call times scaled to the batched mean), per anchor the mean gain
//!    and time, per state the leader, the rival and the exact class, and
//!    per (instance, operator) the batched and per-call means averaged over
//!    states.
//!
//! Every stage resumes: a state whose part file exists is skipped.  The
//! robustness factors of §9 are run options (storage, candidate cache,
//! representation), recorded in `config.json`.
//!
//! Run directory: `config.json`; `gains.parts/`, `gains.meta/`,
//! `timings.parts/` (per call), `batches.parts/`, `timings.meta/`,
//! `nodes.parts/` (one file per state: they are too large to merge);
//! `cells.parquet`, `leaders.parquet` and `operator_times.parquet`.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

use anyhow::{Context, bail};
use serde::{Deserialize, Serialize};
use serde_json::json;

use super::common::{
    CLASSES, EARLY_STATES, Implementation, Job, Pass, Sample, StateRecord, all_cpus,
    batched_counts, batched_time, dispatch, improving_replays, instance_family, load_instance,
    parallel_map, read_states_directory, sampled_batch, state_rank, timed_restores, tour_of,
    warm_up,
};
use super::{mean, quantile, standard_deviation};
use crate::City;
use crate::instance::Instance;
use crate::instance::storage::Dist;
use crate::io::parquet::{
    Column, Table, column_bool, column_f64, column_i64, column_str, has_part, parts_directory,
    read, write, write_part,
};
use crate::io::provenance::write_config;
use crate::io::rundir::write_atomic;
use crate::ops::{Op, Problem, Work};
use crate::rng::{Stream, label_hash};
use crate::timing::cycles;
use crate::timing::machine;
use crate::timing::pin;
use crate::timing::tsc::{self, Clock};
use crate::tour::Layout;

/// Anchors per operator in the pilot that sizes a state.
pub const PILOT_ANCHORS: usize = 6;

/// Timed passes over the anchors per operator and round, at most (per
/// call, batched, batched with two restores): sizes the anchor sample.
const PASSES_PER_ROUND: f64 = 3.0;

/// Length of each worker's reference loop for the core frequency (ms).
const REFERENCE_MS: u64 = 200;

/// Settings of one measurement-A run (shared by every stage; a later stage
/// refuses a run directory started with other settings).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Settings {
    /// Directory of the states files.
    pub states: String,
    /// Applications per (operator, anchor) of the random operators in
    /// pass 1 (deterministic operators run once).
    pub repeats: usize,
    /// Seed of the random streams and the bootstrap.
    pub seed: u64,
    /// Bootstrap resamples per interval.
    pub bootstrap: usize,
    /// Instance names to include (all when empty).
    pub instances: Vec<String>,
    /// State seeds to include (all when empty).
    pub seeds: Vec<u64>,
    /// State names to include (all when empty).
    pub stages: Vec<String>,
    /// Target timing time per state; `None`: always every anchor, no cap.
    pub state_seconds: Option<f64>,
    /// Fewest anchors per state when the budget is tight.
    pub min_anchors: usize,
    /// Early states are skipped on instances larger than this.
    pub early_max_n: Option<usize>,
    /// Timed rounds always made per state.
    pub min_rounds: usize,
    /// Timed rounds at most.
    pub max_rounds: usize,
    /// Stop once every deterministic operator's mean time is known to
    /// within this fraction (95% interval over round means).
    pub precision: f64,
    /// Storage, cache and representation.
    pub implementation: Implementation,
    /// Another measurement-A run whose anchors every state reuses instead
    /// of drawing its own (robustness factors: the sample is then the main
    /// run's, so a changed leader is not a changed sample).  The run must
    /// have the state's gains.
    #[serde(default)]
    pub anchors_from: Option<String>,
}

impl Settings {
    /// Every anchor, no time cap (small instance sets).
    pub fn full(states: String) -> Self {
        Self {
            states,
            repeats: 8,
            seed: 0,
            bootstrap: 1000,
            instances: Vec::new(),
            seeds: Vec::new(),
            stages: Vec::new(),
            state_seconds: None,
            min_anchors: 32,
            early_max_n: None,
            min_rounds: 2,
            max_rounds: 8,
            precision: 0.02,
            implementation: Implementation::default(),
            anchors_from: None,
        }
    }

    /// The design of the E1 main run: 32 repeats, a timing budget per
    /// state, early states skipped above n = 1000.
    pub fn budgeted(states: String) -> Self {
        Self {
            repeats: 32,
            state_seconds: Some(1.0),
            early_max_n: Some(1000),
            ..Self::full(states)
        }
    }

    /// A dry run on three states of kroA100.
    pub fn quick(states: String) -> Self {
        Self {
            repeats: 2,
            bootstrap: 200,
            instances: vec!["kroA100".into()],
            seeds: vec![0],
            stages: vec!["random".into(), "opt2".into(), "lkopt".into()],
            ..Self::full(states)
        }
    }

    fn gain_repeats(&self, op: Op) -> usize {
        if op.deterministic() { 1 } else { self.repeats }
    }
}

/// One state to measure.
#[derive(Debug, Clone)]
pub struct StateTask {
    /// Instance path relative to the repository root.
    pub path: String,
    /// Instance name.
    pub instance: String,
    /// Number of cities.
    pub n: usize,
    /// State seed.
    pub seed: u64,
    /// The state.
    pub state: StateRecord,
}

impl StateTask {
    /// Identifier used in random streams (as in Python).
    pub fn label(&self) -> String {
        format!("{}:s{}:{}", self.instance, self.seed, self.state.name)
    }

    /// File-name key of the state's part files.
    pub fn key(&self) -> String {
        format!("{}_s{}_{}", self.instance, self.seed, self.state.name)
    }
}

/// The states to measure: duplicates and filtered states dropped, early
/// states on large instances dropped when `early_max_n` says so, large
/// and early states first (they take longest).
///
/// # Errors
///
/// If the states directory cannot be read.
pub fn select_tasks(repository: &Path, settings: &Settings) -> anyhow::Result<Vec<StateTask>> {
    let directory = resolve(repository, &settings.states);
    let files = read_states_directory(&directory)?;
    let mut tasks = Vec::new();
    for file in files {
        if !settings.instances.is_empty() && !settings.instances.contains(&file.instance) {
            continue;
        }
        if !settings.seeds.is_empty() && !settings.seeds.contains(&file.state_seed) {
            continue;
        }
        for state in file.states {
            if state.duplicate_of.is_some() {
                continue;
            }
            if !settings.stages.is_empty() && !settings.stages.contains(&state.name) {
                continue;
            }
            if settings
                .early_max_n
                .is_some_and(|limit| file.n > limit && EARLY_STATES.contains(&state.name.as_str()))
            {
                continue;
            }
            tasks.push(StateTask {
                path: file.path.clone(),
                instance: file.instance.clone(),
                n: file.n,
                seed: file.state_seed,
                state,
            });
        }
    }
    tasks.sort_by(|a, b| {
        (
            std::cmp::Reverse(a.n),
            !EARLY_STATES.contains(&a.state.name.as_str()),
            &a.instance,
            a.seed,
            state_rank(&a.state.name),
        )
            .cmp(&(
                std::cmp::Reverse(b.n),
                !EARLY_STATES.contains(&b.state.name.as_str()),
                &b.instance,
                b.seed,
                state_rank(&b.state.name),
            ))
    });
    Ok(tasks)
}

fn resolve(repository: &Path, path: &str) -> PathBuf {
    let path = Path::new(path);
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        repository.join(path)
    }
}

/// The operators measured on an instance (O17/O18 need alpha candidates,
/// which `load_instance` guarantees).
fn operators() -> Vec<Op> {
    Op::ALL.to_vec()
}

// ------------------------------------------------------------- 1. gains

/// Exact local-optimality flags: whether some anchor improves under each
/// operator.  Each check stops at the first improving anchor.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub struct Flags {
    /// O7 improves somewhere.
    pub o7: bool,
    /// The full 2-opt scan O5 improves somewhere.
    pub o5: bool,
    /// O9 (neighbour-list Or-opt) improves somewhere.
    pub oropt: bool,
    /// O10 (neighbour-list 3-opt) improves somewhere.
    pub three_opt: bool,
    /// The LK step (O18) improves somewhere.
    pub lk: bool,
}

impl Flags {
    /// Class index into [`CLASSES`] (`aos.analysis.e1_marginal.exact_class`).
    pub fn class(&self) -> usize {
        if self.o7 || self.o5 {
            0
        } else if self.oropt || self.three_opt {
            1
        } else if self.lk {
            2
        } else {
            3
        }
    }
}

/// Computes [`Flags`] of a tour.
pub fn flags<D: Dist, const C: bool, L: Layout>(
    tour: &crate::tour::Tour<L>,
    problem: &Problem<'_, D, C>,
) -> Flags {
    let n = tour.n() as City;
    let mut rng = Stream::new(0, "flags", &[]);
    // Each check stops at the first improving city, which changes the
    // tour, so every check works on a fresh copy.
    let mut improves = |op: Op| {
        let mut scratch = tour.clone();
        (0..n).any(|city| {
            let delta = op
                .apply(
                    &mut scratch,
                    problem,
                    &mut rng,
                    &mut Work::default(),
                    Some(city),
                )
                .delta;
            scratch.clear_log();
            delta < 0
        })
    };
    let lk = if problem.alpha.is_some() {
        Op::LinKernighanAlpha
    } else {
        Op::LinKernighan
    };
    let o7 = improves(Op::TwoOptNl);
    let o5 = improves(Op::TwoOptFirst);
    let oropt = improves(Op::OrOptNl);
    let three_opt = improves(Op::ThreeOptNl);
    let lk = improves(lk);
    Flags {
        o7,
        o5,
        oropt,
        three_opt,
        lk,
    }
}

/// Exact class index of a tour (into [`CLASSES`]), short-circuiting: O7
/// or O5 first, then O9 or O10, then the LK step; the same rule as
/// [`Flags::class`].  The tour is not changed.
pub fn classify<D: Dist, const C: bool, L: Layout>(
    tour: &crate::tour::Tour<L>,
    problem: &Problem<'_, D, C>,
) -> usize {
    let n = tour.n() as City;
    let mut rng = Stream::new(0, "flags", &[]);
    let mut scratch = tour.clone();
    let mut improves = |ops: &[Op]| {
        ops.iter().any(|&op| {
            (0..n).any(|city| {
                let mark = scratch.mark();
                let delta = op
                    .apply(
                        &mut scratch,
                        problem,
                        &mut rng,
                        &mut Work::default(),
                        Some(city),
                    )
                    .delta;
                scratch.undo_to(mark);
                delta < 0
            })
        })
    };
    let lk = if problem.alpha.is_some() {
        Op::LinKernighanAlpha
    } else {
        Op::LinKernighan
    };
    if improves(&[Op::TwoOptNl, Op::TwoOptFirst]) {
        0
    } else if improves(&[Op::OrOptNl, Op::ThreeOptNl]) {
        1
    } else if improves(&[lk]) {
        2
    } else {
        3
    }
}

/// What pass 1 stores per state besides the applications.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GainMeta {
    /// The anchors, sorted.
    pub anchors: Vec<City>,
    /// Whether the anchors are a sample rather than every city.
    pub sampled: bool,
    /// Exact local-optimality flags.
    pub flags: Flags,
    /// Wall time of the stage for this state.
    pub seconds: f64,
    /// Tour representation used.
    pub layout: String,
    /// Length of the state tour (recomputed with the Rust distances).
    pub length: i64,
    /// Untimed estimate of one timed round (every operator once per
    /// anchor), ns: for sizing runs.
    pub round_ns_estimate: f64,
}

#[derive(Default)]
struct GainRows {
    operator: Vec<String>,
    anchor: Vec<u32>,
    repeat: Vec<u32>,
    gain: Vec<i64>,
    matrix_reads: Vec<i64>,
    cache_reads: Vec<i64>,
    positions: Vec<i64>,
    flips: Vec<i64>,
    tour_hash: Vec<i64>,
    wall_ns: Vec<i64>,
}

impl GainRows {
    fn into_table(self) -> Table {
        Table::new()
            .with("operator", Column::Str(self.operator))
            .with("anchor", Column::U32(self.anchor))
            .with("repeat", Column::U32(self.repeat))
            .with("gain", Column::I64(self.gain))
            .with("matrix_reads", Column::I64(self.matrix_reads))
            .with("cache_reads", Column::I64(self.cache_reads))
            .with("positions", Column::I64(self.positions))
            .with("flips", Column::I64(self.flips))
            .with("tour_hash", Column::I64(self.tour_hash))
            .with("wall_ns", Column::I64(self.wall_ns))
    }
}

/// The stream of one application: (seed, state label, operator, anchor,
/// repeat), so any application replays alone, in any order, in either pass.
fn application_stream(seed: u64, label: &str, op: Op, anchor: City, repeat: usize) -> Stream {
    Stream::new(seed, label, &[op as u64, u64::from(anchor), repeat as u64])
}

/// Applies every operator at every anchor from the state tour, untimed.
#[allow(clippy::too_many_arguments)]
fn measure_gains<D: Dist, const C: bool, L: Layout>(
    tour: &mut crate::tour::Tour<L>,
    problem: &Problem<'_, D, C>,
    ops: &[Op],
    anchors: &[City],
    repeats: impl Fn(Op) -> usize,
    seed: u64,
    label: &str,
    rows: &mut GainRows,
) -> anyhow::Result<()> {
    let length = tour.length();
    for &op in ops {
        for &anchor in anchors {
            for repeat in 0..repeats(op) {
                let mut rng = application_stream(seed, label, op, anchor, repeat);
                let mut work = Work::default();
                let mark = tour.mark();
                let started = std::time::Instant::now();
                let application = op.apply(tour, problem, &mut rng, &mut work, Some(anchor));
                let wall = started.elapsed().as_nanos() as i64;
                if application.delta > 0 {
                    bail!("{label}: {} lengthened the tour", op.name());
                }
                let hash = if application.delta < 0 {
                    tour.hash() as i64
                } else {
                    0
                };
                tour.undo_to(mark);
                if tour.length() != length {
                    bail!("{label}: {} was not restored", op.name());
                }
                rows.operator.push(op.name().to_string());
                rows.anchor.push(anchor);
                rows.repeat.push(repeat as u32);
                rows.gain.push(-application.delta);
                rows.matrix_reads.push(work.matrix_reads as i64);
                rows.cache_reads.push(work.cache_reads as i64);
                rows.positions.push(work.positions as i64);
                rows.flips.push(work.flips as i64);
                rows.tour_hash.push(hash);
                rows.wall_ns.push(wall);
            }
        }
    }
    Ok(())
}

struct GainJob<'a> {
    task: &'a StateTask,
    settings: &'a Settings,
}

impl Job for GainJob<'_> {
    type Output = (GainRows, GainMeta);

    fn run<D: Dist, const C: bool, L: Layout>(
        self,
        instance: &Instance,
        problem: &Problem<'_, D, C>,
    ) -> anyhow::Result<Self::Output> {
        let started = std::time::Instant::now();
        let settings = self.settings;
        let label = self.task.label();
        let n = instance.n();
        let mut tour = tour_of::<L>(instance, &self.task.state.order);
        let flags = flags(&tour, problem);
        let ops = operators();
        let anchors: Vec<City> = match (&settings.anchors_from, settings.state_seconds) {
            (Some(other), _) => {
                let path = meta_path(Path::new(other), "gains", &self.task.key());
                let meta: GainMeta = serde_json::from_str(
                    &std::fs::read_to_string(&path)
                        .with_context(|| format!("{label}: reading {}", path.display()))?,
                )?;
                meta.anchors
            }
            (None, None) => (0..n as City).collect(),
            (None, Some(seconds)) => {
                let mut rng = Stream::new(settings.seed, "anchors", &[label_hash(&label)]);
                let pilot: Vec<City> = rng
                    .sample(n, PILOT_ANCHORS.min(n))
                    .into_iter()
                    .map(|city| city as City)
                    .collect();
                let mut rows = GainRows::default();
                measure_gains(
                    &mut tour,
                    problem,
                    &ops,
                    &pilot,
                    |_| 1,
                    settings.seed + 1,
                    &format!("pilot:{label}"),
                    &mut rows,
                )?;
                let per_anchor_ns = rows.wall_ns.iter().sum::<i64>() as f64 / pilot.len() as f64;
                let affordable = (seconds * 1e9
                    / (settings.min_rounds as f64 * PASSES_PER_ROUND * per_anchor_ns.max(1.0)))
                    as usize;
                let count = n.min(settings.min_anchors.max(affordable));
                if count >= n {
                    (0..n as City).collect()
                } else {
                    let mut chosen: Vec<City> = rng
                        .sample(n, count)
                        .into_iter()
                        .map(|city| city as City)
                        .collect();
                    chosen.sort_unstable();
                    chosen
                }
            }
        };
        let mut rows = GainRows::default();
        measure_gains(
            &mut tour,
            problem,
            &ops,
            &anchors,
            |op| settings.gain_repeats(op),
            settings.seed,
            &label,
            &mut rows,
        )?;
        let round_ns_estimate = rows
            .operator
            .iter()
            .zip(&rows.wall_ns)
            .map(|(name, wall)| {
                let op = Op::from_name(name).expect("operator names come from Op");
                *wall as f64 / settings.gain_repeats(op) as f64
            })
            .sum();
        let meta = GainMeta {
            round_ns_estimate,
            sampled: anchors.len() < n,
            anchors,
            flags,
            seconds: started.elapsed().as_secs_f64(),
            layout: L::NAME.to_string(),
            length: tour.length(),
        };
        Ok((rows, meta))
    }
}

/// Instances loaded once per thread (tasks come grouped by instance).
struct InstanceCache {
    cached: Option<(String, std::sync::Arc<Instance>)>,
}

impl InstanceCache {
    fn get(&mut self, repository: &Path, path: &str) -> anyhow::Result<std::sync::Arc<Instance>> {
        if let Some((cached_path, instance)) = &self.cached
            && cached_path == path
        {
            return Ok(instance.clone());
        }
        self.cached = None;
        let instance = std::sync::Arc::new(load_instance(repository, path)?);
        self.cached = Some((path.to_string(), instance.clone()));
        Ok(instance)
    }
}

thread_local! {
    static INSTANCES: std::cell::RefCell<InstanceCache> =
        const { std::cell::RefCell::new(InstanceCache { cached: None }) };
}

fn instance_for(repository: &Path, path: &str) -> anyhow::Result<std::sync::Arc<Instance>> {
    INSTANCES.with(|cache| cache.borrow_mut().get(repository, path))
}

fn meta_path(directory: &Path, kind: &str, key: &str) -> PathBuf {
    directory
        .join(format!("{kind}.meta"))
        .join(format!("{key}.json"))
}

/// Stage 1 on every state not yet done.
///
/// # Errors
///
/// If a state fails (the error names it).
pub fn run_gains(
    repository: &Path,
    directory: &Path,
    settings: &Settings,
    tasks: &[StateTask],
    workers: usize,
) -> anyhow::Result<()> {
    let todo: Vec<&StateTask> = tasks
        .iter()
        .filter(|task| !has_part(directory, "gains", &task.key()))
        .collect();
    log::info!(
        "gains: {} states to do, {} done, {workers} threads",
        todo.len(),
        tasks.len() - todo.len()
    );
    std::fs::create_dir_all(directory.join("gains.meta"))?;
    let done = AtomicUsize::new(0);
    let started = std::time::Instant::now();
    parallel_map(
        &todo,
        workers,
        |task| task.label(),
        |task| {
            let instance = instance_for(repository, &task.path)?;
            let (rows, meta) = dispatch(
                GainJob { task, settings },
                &instance,
                &settings.implementation,
            )?;
            write_atomic(
                &meta_path(directory, "gains", &task.key()),
                serde_json::to_string(&meta)?.as_bytes(),
            )?;
            // The part file is written last: its presence marks the state done.
            write_part(directory, "gains", &task.key(), rows.into_table())?;
            let count = done.fetch_add(1, Ordering::Relaxed) + 1;
            if count.is_multiple_of(100) || count == todo.len() {
                log::info!(
                    "gains progress: {count}/{} states, {:.0} s",
                    todo.len(),
                    started.elapsed().as_secs_f64()
                );
            }
            Ok(())
        },
    )?;
    Ok(())
}

// -------------------------------------------------------------- 2. times

/// Pass-1 results of one state, for replay checks.
struct Expected {
    by_application: HashMap<(Op, City, u32), (i64, i64)>,
    anchors: Vec<City>,
}

impl Expected {
    /// Pass-1 gain and tour hash of one application.
    fn get(&self, label: &str, op: Op, anchor: City, repeat: usize) -> anyhow::Result<(i64, i64)> {
        self.by_application
            .get(&(op, anchor, repeat as u32))
            .copied()
            .with_context(|| format!("{label}: no pass-1 result for {} at {anchor}", op.name()))
    }
}

fn read_expected(directory: &Path, key: &str) -> anyhow::Result<Expected> {
    let batch = read(&parts_directory(directory, "gains").join(format!("{key}.parquet")))?;
    let operator = column_str(&batch, "operator")?;
    let anchor = column_i64(&batch, "anchor")?;
    let repeat = column_i64(&batch, "repeat")?;
    let gain = column_i64(&batch, "gain")?;
    let hash = column_i64(&batch, "tour_hash")?;
    let mut by_application = HashMap::with_capacity(operator.len());
    let mut anchors: Vec<City> = Vec::new();
    for index in 0..operator.len() {
        // Gains written before 2026-11-05 still hold the retired operators.
        let Some(op) = Op::parse_stored(&operator[index])? else {
            continue;
        };
        by_application.insert(
            (op, anchor[index] as City, repeat[index] as u32),
            (gain[index], hash[index]),
        );
        anchors.push(anchor[index] as City);
    }
    anchors.sort_unstable();
    anchors.dedup();
    Ok(Expected {
        by_application,
        anchors,
    })
}

/// How pass 2 timed one state.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TimingMeta {
    /// Rounds timed.
    pub rounds: usize,
    /// Why timing stopped: `precision`, `budget` or `max_rounds`.
    pub stop_reason: String,
    /// Wall time of the timing.
    pub seconds: f64,
    /// The core it ran on.
    pub cpu: usize,
    /// Kernel's average core frequency before and after (kHz, a snapshot
    /// of the last scheduler tick), when readable.
    pub khz_before: Option<f64>,
    /// See `khz_before`.
    pub khz_after: Option<f64>,
    /// Average core frequency over the whole timing of the state, from the
    /// thread's core-cycle counter (kHz; a slight lower bound, see
    /// [`crate::timing::cycles`]).
    #[serde(default)]
    pub core_khz: Option<f64>,
    /// The worker's reference frequency, measured on its core before its
    /// first state (kHz).
    #[serde(default)]
    pub reference_khz: Option<f64>,
    /// Whether the frequency moved by more than 1%: between the two
    /// snapshots, or between the reference and the state's average.
    pub suspect: bool,
    /// Clock overhead subtracted from every per-call time (ns): the mean
    /// of empty timed regions.
    pub overhead_ns: f64,
    /// The counter's step (ns): the resolution of a per-call time.
    #[serde(default)]
    pub step_ns: f64,
}

/// One row per timed application of the per-call pass.
#[derive(Default)]
struct TimeRows {
    operator: Vec<String>,
    anchor: Vec<u32>,
    round: Vec<u32>,
    repeat: Vec<u32>,
    time_ns: Vec<f64>,
}

/// One row per (operator, round) of the batched passes.
#[derive(Default)]
struct BatchRows {
    operator: Vec<String>,
    round: Vec<u32>,
    applications: Vec<u32>,
    /// One region around every anchor: apply, restore.
    batch_ns: Vec<f64>,
    /// One region around replaying every improving application's changes
    /// and restoring them (about two restores each); absent when no
    /// application improves (nothing is restored).
    restore_batch_ns: Vec<Option<f64>>,
    /// Estimated restore time of the whole batch: half the restore batch.
    restore_ns: Vec<f64>,
    /// Mean time per application, restore removed: the operator's time.
    time_ns: Vec<f64>,
    /// User-space instructions of the batch region (its median sample);
    /// absent where the performance counters cannot be read.
    batch_instructions: Vec<Option<f64>>,
    /// User-space core cycles of the batch region (its median sample).
    batch_cycles: Vec<Option<f64>>,
    /// Instructions per application, restore removed as for `time_ns`.
    instructions: Vec<Option<f64>>,
    /// Cycles per application, restore removed as for `time_ns`.
    cycles: Vec<Option<f64>>,
}

impl BatchRows {
    fn into_table(self) -> Table {
        Table::new()
            .with("operator", Column::Str(self.operator))
            .with("round", Column::U32(self.round))
            .with("applications", Column::U32(self.applications))
            .with("batch_ns", Column::F64(self.batch_ns))
            .with("restore_batch_ns", Column::OptF64(self.restore_batch_ns))
            .with("restore_ns", Column::F64(self.restore_ns))
            .with("time_ns", Column::F64(self.time_ns))
            .with(
                "batch_instructions",
                Column::OptF64(self.batch_instructions),
            )
            .with("batch_cycles", Column::OptF64(self.batch_cycles))
            .with("instructions", Column::OptF64(self.instructions))
            .with("cycles", Column::OptF64(self.cycles))
    }
}

/// Whether the 95% t-interval of the mean of `values` lies within
/// `precision` times that mean.
fn precise(values: &[f64], precision: f64) -> bool {
    let count = values.len();
    if count < 2 {
        return false;
    }
    use statrs::distribution::{ContinuousCDF, StudentsT};
    let centre = mean(values);
    let spread = standard_deviation(values);
    // Until 2026-11-02 a table for 1–10 degrees of freedom, its last value
    // (2.228) reused above 10: too wide an interval for larger samples.
    let t = StudentsT::new(0.0, 1.0, (count - 1) as f64)
        .expect("positive degrees of freedom")
        .inverse_cdf(0.975);
    t * spread / (count as f64).sqrt() <= precision * centre.abs()
}

/// What every pass of one (operator, round) shares.
struct OperatorRound<'a> {
    label: &'a str,
    seed: u64,
    op: Op,
    repeat: usize,
    anchors: &'a [City],
    /// Pass-1 gain and tour hash of each anchor, in `anchors` order.
    expected: &'a [(i64, i64)],
    clock: &'a Clock,
}

impl OperatorRound<'_> {
    fn mismatch(&self, anchor: City, gain: i64, expected: i64) -> anyhow::Error {
        anyhow::anyhow!(
            "{}: {} at anchor {anchor}, repeat {} gained {gain}, pass 1 gained {expected}",
            self.label,
            self.op.name(),
            self.repeat
        )
    }

    /// One region per application; every replay must reproduce its pass-1
    /// gain and tour hash.
    fn per_call<D: Dist, const C: bool, L: Layout>(
        &self,
        tour: &mut crate::tour::Tour<L>,
        problem: &Problem<'_, D, C>,
        round: usize,
        rows: &mut TimeRows,
    ) -> anyhow::Result<()> {
        let length = tour.length();
        for (&anchor, &(gain, hash)) in self.anchors.iter().zip(self.expected) {
            let mut rng = application_stream(self.seed, self.label, self.op, anchor, self.repeat);
            let mut work = Work::default();
            let mark = tour.mark();
            let begin = tsc::start();
            let application = self
                .op
                .apply(tour, problem, &mut rng, &mut work, Some(anchor));
            let end = tsc::stop();
            if -application.delta != gain {
                return Err(self.mismatch(anchor, -application.delta, gain));
            }
            if application.delta < 0 && tour.hash() as i64 != hash {
                bail!(
                    "{}: {} at anchor {anchor}, repeat {} reached another tour than in pass 1",
                    self.label,
                    self.op.name(),
                    self.repeat
                );
            }
            tour.undo_to(mark);
            if tour.length() != length {
                bail!("{}: {} was not restored", self.label, self.op.name());
            }
            rows.operator.push(self.op.name().to_string());
            rows.anchor.push(anchor);
            rows.round.push(round as u32);
            rows.repeat.push(self.repeat as u32);
            rows.time_ns.push(self.clock.region_ns(begin, end));
        }
        Ok(())
    }

    /// One region around every application of the round, each restored
    /// to the state inside it; returns the region's median sample.  The
    /// streams are built and the gains checked outside the region.
    fn batch<D: Dist, const C: bool, L: Layout>(
        &self,
        tour: &mut crate::tour::Tour<L>,
        problem: &Problem<'_, D, C>,
    ) -> anyhow::Result<Sample> {
        let length = tour.length();
        let mut deltas = vec![0i64; self.anchors.len()];
        let elapsed = sampled_batch(
            tour,
            problem,
            self.op,
            self.anchors,
            |anchor| application_stream(self.seed, self.label, self.op, anchor, self.repeat),
            self.clock,
            &mut deltas,
        )
        .with_context(|| self.label.to_string())?;
        for ((&anchor, &(gain, _)), &delta) in self.anchors.iter().zip(self.expected).zip(&deltas) {
            if -delta != gain {
                return Err(self.mismatch(anchor, -delta, gain));
            }
        }
        if tour.length() != length {
            bail!("{}: {} was not restored", self.label, self.op.name());
        }
        Ok(elapsed)
    }

    /// One region around replaying the changes of every improving
    /// application (recorded untimed first) and restoring them; returns
    /// the region's median sample.
    fn restores<D: Dist, const C: bool, L: Layout>(
        &self,
        tour: &mut crate::tour::Tour<L>,
        problem: &Problem<'_, D, C>,
    ) -> anyhow::Result<Sample> {
        let improving: Vec<City> = self
            .anchors
            .iter()
            .zip(self.expected)
            .filter(|(_, (gain, _))| *gain > 0)
            .map(|(&anchor, _)| anchor)
            .collect();
        let replays = improving_replays(tour, problem, self.op, &improving, |anchor| {
            application_stream(self.seed, self.label, self.op, anchor, self.repeat)
        });
        if replays.len() != improving.len() {
            bail!(
                "{}: {} improved at {} of the {} anchors where pass 1 improved",
                self.label,
                self.op.name(),
                replays.len(),
                improving.len()
            );
        }
        let length = tour.length();
        let elapsed = timed_restores(tour, &replays, self.clock);
        if tour.length() != length {
            bail!(
                "{}: replays of {} were not restored",
                self.label,
                self.op.name()
            );
        }
        Ok(elapsed)
    }
}

struct TimeJob<'a> {
    task: &'a StateTask,
    settings: &'a Settings,
    expected: &'a Expected,
    clock: &'a Clock,
}

impl Job for TimeJob<'_> {
    type Output = (TimeRows, BatchRows, usize, String);

    /// Rounds over every operator, in a shuffled order; each operator runs
    /// over every anchor (shuffled per round) back to back, as a search
    /// runs it during a slice, in up to three passes: per call, batched
    /// (each application restored inside the region), and a restore batch
    /// when some application improves (replays of the improving
    /// applications' changes, each restored: half of it is the restore time
    /// removed from the batched time).  The batched time per application is the operator's
    /// time; the per-call times give its spread over anchors.
    fn run<D: Dist, const C: bool, L: Layout>(
        self,
        instance: &Instance,
        problem: &Problem<'_, D, C>,
    ) -> anyhow::Result<Self::Output> {
        let settings = self.settings;
        let label = self.task.label();
        let mut tour = tour_of::<L>(instance, &self.task.state.order);
        crate::timing::disturb::spin_up(crate::timing::disturb::SPIN_UP_MS);
        let mut ops = operators();
        let judged: Vec<Op> = ops
            .iter()
            .copied()
            .filter(|op| op.deterministic())
            .collect();
        let mut anchors = self.expected.anchors.clone();
        let mut order = Stream::new(settings.seed, "marginal-order", &[label_hash(&label)]);
        let mut rows = TimeRows::default();
        let mut batches = BatchRows::default();
        let mut round_means: HashMap<Op, Vec<f64>> = HashMap::new();
        let started = std::time::Instant::now();
        let mut reason = "max_rounds".to_string();
        let mut rounds = 0;
        for round in 0..settings.max_rounds {
            order.shuffle(&mut ops);
            for &op in &ops {
                let repeat = round % settings.gain_repeats(op);
                order.shuffle(&mut anchors);
                let expected: Vec<(i64, i64)> = anchors
                    .iter()
                    .map(|&anchor| self.expected.get(&label, op, anchor, repeat))
                    .collect::<anyhow::Result<_>>()?;
                let shared = OperatorRound {
                    label: &label,
                    seed: settings.seed,
                    op,
                    repeat,
                    anchors: &anchors,
                    expected: &expected,
                    clock: self.clock,
                };
                let improves = expected.iter().any(|&(gain, _)| gain > 0);
                let (mut single, mut restores) = (None, None);
                warm_up(&mut tour, problem, op, &anchors, |anchor| {
                    application_stream(settings.seed, &label, op, anchor, repeat)
                });
                for pass in Pass::order(improves, &mut order) {
                    match pass {
                        Pass::PerCall => shared.per_call(&mut tour, problem, round, &mut rows)?,
                        Pass::Batch => single = Some(shared.batch(&mut tour, problem)?),
                        Pass::Restore => restores = Some(shared.restores(&mut tour, problem)?),
                    }
                }
                let single = single.expect("the batch pass always runs");
                let (restore, per_application) =
                    batched_time(single.ns, restores.map(|sample| sample.ns), anchors.len());
                let counts = batched_counts(single, restores, anchors.len());
                batches.operator.push(op.name().to_string());
                batches.round.push(round as u32);
                batches.applications.push(anchors.len() as u32);
                batches.batch_ns.push(single.ns);
                batches
                    .restore_batch_ns
                    .push(restores.map(|sample| sample.ns));
                batches
                    .batch_instructions
                    .push(single.counts.map(|counts| counts.instructions));
                batches
                    .batch_cycles
                    .push(single.counts.map(|counts| counts.cycles));
                batches
                    .instructions
                    .push(counts.map(|counts| counts.instructions));
                batches.cycles.push(counts.map(|counts| counts.cycles));
                batches.restore_ns.push(restore);
                batches.time_ns.push(per_application);
                round_means.entry(op).or_default().push(per_application);
            }
            rounds = round + 1;
            if rounds < settings.min_rounds {
                continue;
            }
            if judged
                .iter()
                .all(|op| precise(&round_means[op], settings.precision))
            {
                reason = "precision".into();
                break;
            }
            if settings
                .state_seconds
                .is_some_and(|seconds| started.elapsed().as_secs_f64() >= seconds)
            {
                reason = "budget".into();
                break;
            }
        }
        Ok((rows, batches, rounds, reason))
    }
}

/// Stage 2: times every state not yet timed on `workers` pinned threads.
///
/// # Errors
///
/// If a state has no pass-1 result, a replay disagrees with pass 1, the
/// machine is not locked (without `allow_unlocked`), or too few cores
/// exist.
pub fn run_times(
    repository: &Path,
    directory: &Path,
    settings: &Settings,
    tasks: &[StateTask],
    workers: usize,
    allow_unlocked: bool,
) -> anyhow::Result<()> {
    if let Some(missing) = tasks
        .iter()
        .find(|task| !has_part(directory, "gains", &task.key()))
    {
        bail!(
            "{} has no gains yet; run the gains stage first",
            missing.label()
        );
    }
    let cores = pin::physical_cores()?;
    if workers + 1 > cores.len() {
        bail!(
            "{workers} timed workers need {} physical cores, found {}",
            workers + 1,
            cores.len()
        );
    }
    let problems = machine::require_locked(&cores, allow_unlocked)?;
    pin::pin_current_thread(*cores.last().expect("cores exist"))?;
    let clock = Clock::calibrate(5, 200)?;
    let todo: Vec<&StateTask> = tasks
        .iter()
        .filter(|task| !has_part(directory, "timings", &task.key()))
        .collect();
    let stage = json!({
        "started": crate::io::rundir::timestamp(),
        "machine_problems": problems,
        "clock": clock,
        "timed_workers": workers,
        "cores": &cores[..workers],
        "main_core": cores.last(),
        "states_to_do": todo.len(),
    });
    let stage_file = directory.join(format!("times-{}.json", crate::io::rundir::timestamp()));
    write_atomic(
        &stage_file,
        (serde_json::to_string_pretty(&stage)? + "\n").as_bytes(),
    )?;
    log::info!(
        "times: {} states to do, {} done, {workers} timed worker(s)",
        todo.len(),
        tasks.len() - todo.len()
    );
    std::fs::create_dir_all(directory.join("timings.meta"))?;
    let next = AtomicUsize::new(0);
    let done = AtomicUsize::new(0);
    let errors: Mutex<Vec<anyhow::Error>> = Mutex::new(Vec::new());
    let started = std::time::Instant::now();
    std::thread::scope(|scope| {
        for &cpu in &cores[..workers] {
            let (todo, next, done, errors, clock) = (&todo, &next, &done, &errors, &clock);
            scope.spawn(move || {
                let result = (|| -> anyhow::Result<()> {
                    pin::pin_current_thread(cpu)?;
                    // Opened on this thread: they count this thread only.
                    let mut counters = if allow_unlocked {
                        cycles::ThreadCounters::open()
                            .inspect_err(|error| {
                                log::warn!("cpu {cpu}: no core-cycle counter: {error:#}");
                            })
                            .ok()
                    } else {
                        Some(cycles::ThreadCounters::open()?)
                    };
                    let reference_khz = match counters.as_mut() {
                        Some(counters) => Some(counters.reference_khz(REFERENCE_MS)?),
                        None => None,
                    };
                    loop {
                        if !errors.lock().expect("lock").is_empty() {
                            return Ok(());
                        }
                        let index = next.fetch_add(1, Ordering::Relaxed);
                        let Some(task) = todo.get(index) else {
                            return Ok(());
                        };
                        let key = task.key();
                        let expected = read_expected(directory, &key)?;
                        let instance = instance_for(repository, &task.path)?;
                        let khz_before = machine::average_khz(cpu).map(|value| value as f64);
                        let reading_before = match counters.as_mut() {
                            Some(counters) => Some(counters.read()?),
                            None => None,
                        };
                        let state_started = std::time::Instant::now();
                        let (rows, batches, rounds, reason) = dispatch(
                            TimeJob {
                                task,
                                settings,
                                expected: &expected,
                                clock,
                            },
                            &instance,
                            &settings.implementation,
                        )
                        .with_context(|| task.label())?;
                        let seconds = state_started.elapsed().as_secs_f64();
                        let core_khz = match (counters.as_mut(), reading_before) {
                            (Some(counters), Some(before)) => counters.read()?.khz_since(&before),
                            _ => None,
                        };
                        let khz_after = machine::average_khz(cpu).map(|value| value as f64);
                        let tolerance = super::e0::FREQUENCY_DRIFT;
                        let meta = TimingMeta {
                            rounds,
                            stop_reason: reason,
                            seconds,
                            cpu,
                            khz_before,
                            khz_after,
                            core_khz,
                            reference_khz,
                            suspect: cycles::drifted(khz_before, khz_after, tolerance)
                                || cycles::drifted(reference_khz, core_khz, tolerance),
                            overhead_ns: clock.overhead_ns(),
                            step_ns: clock.ns(clock.step_ticks),
                        };
                        if meta.suspect {
                            log::warn!(
                                "{}: core frequency drifted (snapshots {khz_before:?} -> {khz_after:?} kHz, average {core_khz:?} against reference {reference_khz:?} kHz); timings flagged as suspect",
                                task.label()
                            );
                        }
                        write_atomic(
                            &meta_path(directory, "timings", &key),
                            serde_json::to_string(&meta)?.as_bytes(),
                        )?;
                        // Batches before timings: the timings part marks the state done.
                        write_part(directory, "batches", &key, batches.into_table())?;
                        write_part(
                            directory,
                            "timings",
                            &key,
                            Table::new()
                                .with("operator", Column::Str(rows.operator))
                                .with("anchor", Column::U32(rows.anchor))
                                .with("round", Column::U32(rows.round))
                                .with("repeat", Column::U32(rows.repeat))
                                .with("time_ns", Column::F64(rows.time_ns)),
                        )?;
                        let count = done.fetch_add(1, Ordering::Relaxed) + 1;
                        if count.is_multiple_of(100) || count == todo.len() {
                            log::info!(
                                "times progress: {count}/{} states, {:.0} s",
                                todo.len(),
                                started.elapsed().as_secs_f64()
                            );
                        }
                    }
                })();
                if let Err(error) = result {
                    errors.lock().expect("lock").push(error);
                }
            });
        }
    });
    if let Some(error) = errors
        .into_inner()
        .expect("workers finished")
        .into_iter()
        .next()
    {
        return Err(error);
    }
    Ok(())
}

// ---------------------------------------------------------- 3. summarise

/// Rate summary of one operator in one state.
#[derive(Debug, Clone, Copy)]
pub struct RateSummary {
    /// Mean gain over mean time, length units per second.
    pub rate: f64,
    /// 2.5% bootstrap quantile.
    pub low: f64,
    /// 97.5% bootstrap quantile.
    pub high: f64,
    /// Mean gain per application.
    pub mean_gain: f64,
    /// Mean time per application, ns.
    pub mean_time_ns: f64,
    /// Fraction of improving applications.
    pub p_improve: f64,
    /// Anchors with at least one improving application.
    pub improving_anchors: usize,
}

/// Rows = anchors, columns = repeats or rounds, as a flat row-major grid.
struct Grid {
    rows: usize,
    width: usize,
    values: Vec<f64>,
}

impl Grid {
    fn build(anchors: &[City], entries: &[(City, u32, f64)]) -> anyhow::Result<Self> {
        let index: HashMap<City, usize> = anchors
            .iter()
            .enumerate()
            .map(|(row, &anchor)| (anchor, row))
            .collect();
        let mut cells: Vec<Vec<(u32, f64)>> = vec![Vec::new(); anchors.len()];
        for &(anchor, column, value) in entries {
            let row = *index
                .get(&anchor)
                .with_context(|| format!("anchor {anchor} not in pass 1"))?;
            cells[row].push((column, value));
        }
        let width = cells.first().map_or(0, Vec::len);
        if width == 0 || cells.iter().any(|cell| cell.len() != width) {
            bail!("every anchor needs the same number of entries");
        }
        let mut values = Vec::with_capacity(anchors.len() * width);
        for mut cell in cells {
            cell.sort_by_key(|(column, _)| *column);
            values.extend(cell.into_iter().map(|(_, value)| value));
        }
        Ok(Self {
            rows: anchors.len(),
            width,
            values,
        })
    }

    fn row_means(&self) -> Vec<f64> {
        self.values
            .chunks(self.width)
            .map(|row| row.iter().sum::<f64>() / self.width as f64)
            .collect()
    }

    /// Bootstrap means with rows fixed and columns resampled within rows.
    fn stratified_means(&self, resamples: usize, rng: &mut Stream) -> Vec<f64> {
        if self.width == 1 {
            let centre = self.values.iter().sum::<f64>() / self.values.len() as f64;
            return vec![centre; resamples];
        }
        let total = (self.rows * self.width) as f64;
        (0..resamples)
            .map(|_| {
                let mut sum = 0.0;
                for row in self.values.chunks(self.width) {
                    for _ in 0..self.width {
                        sum += row[rng.index(self.width)];
                    }
                }
                sum / total
            })
            .collect()
    }
}

/// Summarises one operator in one state from its two passes: the mean
/// over anchors of the per-anchor mean gain over the mean over anchors of
/// the per-anchor mean time; the interval resamples anchors (cluster
/// bootstrap) when they are a sample, else repeats and rounds within each
/// anchor (stratified bootstrap).
///
/// # Errors
///
/// If the passes cover different anchors or the mean time is not positive.
pub fn summarise_operator(
    anchors: &[City],
    gains: &[(City, u32, f64)],
    times: &[(City, u32, f64)],
    sampled: bool,
    resamples: usize,
    rng: &mut Stream,
) -> anyhow::Result<RateSummary> {
    let gain_grid = Grid::build(anchors, gains)?;
    let time_grid = Grid::build(anchors, times)?;
    let gain_means = gain_grid.row_means();
    let time_means = time_grid.row_means();
    let mean_gain = mean(&gain_means);
    let mean_time = mean(&time_means);
    if mean_time <= 0.0 {
        bail!("mean time {mean_time} ns is not positive");
    }
    let rate = mean_gain / (mean_time / 1e9);
    let rates: Vec<f64> = if sampled {
        let count = anchors.len();
        (0..resamples)
            .map(|_| {
                let (mut gain, mut time) = (0.0, 0.0);
                for _ in 0..count {
                    let pick = rng.index(count);
                    gain += gain_means[pick];
                    time += time_means[pick];
                }
                gain / (time / 1e9)
            })
            .collect()
    } else {
        let gains = gain_grid.stratified_means(resamples, rng);
        let times = time_grid.stratified_means(resamples, rng);
        gains
            .iter()
            .zip(&times)
            .map(|(gain, time)| gain / (time / 1e9))
            .collect()
    };
    Ok(RateSummary {
        rate,
        low: quantile(&rates, 0.025),
        high: quantile(&rates, 0.975),
        mean_gain,
        mean_time_ns: mean_time,
        p_improve: gain_grid.values.iter().filter(|&&gain| gain > 0.0).count() as f64
            / gain_grid.values.len() as f64,
        improving_anchors: gain_grid
            .values
            .chunks(gain_grid.width)
            .filter(|row| row.iter().any(|&gain| gain > 0.0))
            .count(),
    })
}

/// The time of one operator in one state from both timed passes.
#[derive(Debug, Clone, Copy)]
pub struct OperatorTime {
    /// Batched time per application, restore removed (ns), the median over
    /// rounds of each round's mean: the operator's time in its rate.
    pub batched_ns: f64,
    /// Mean of the per-call times (ns).
    pub per_call_ns: f64,
    /// Estimated restore time per application (ns), median over rounds.
    pub restore_ns: f64,
}

impl OperatorTime {
    /// Factor that brings per-call times to the batched mean.
    pub fn scale(&self) -> f64 {
        self.batched_ns / self.per_call_ns
    }
}

/// Both passes' times per operator, from a state's `batches` and
/// `timings` tables.
fn operator_times_from(
    key: &str,
    batches: &arrow::record_batch::RecordBatch,
    times: &arrow::record_batch::RecordBatch,
) -> anyhow::Result<HashMap<String, OperatorTime>> {
    // Per operator, every round's time per application and restore per
    // application.
    let mut batched: HashMap<String, (Vec<f64>, Vec<f64>)> = HashMap::new();
    for (((op, time), restore), applications) in column_str(batches, "operator")?
        .into_iter()
        .zip(column_f64(batches, "time_ns")?)
        .zip(column_f64(batches, "restore_ns")?)
        .zip(column_i64(batches, "applications")?)
    {
        let entry = batched.entry(op).or_default();
        entry.0.push(time);
        entry.1.push(restore / applications as f64);
    }
    let mut per_call: HashMap<String, (f64, usize)> = HashMap::new();
    for (op, time) in column_str(times, "operator")?
        .into_iter()
        .zip(column_f64(times, "time_ns")?)
    {
        let entry = per_call.entry(op).or_default();
        entry.0 += time;
        entry.1 += 1;
    }
    batched
        .into_iter()
        .map(|(op, (times, restores))| {
            let (total, count) = *per_call
                .get(&op)
                .with_context(|| format!("{key}: {op} has batches but no per-call times"))?;
            let per_call_ns = total / count as f64;
            if per_call_ns <= 0.0 {
                bail!("{key}: {op} has a mean per-call time of {per_call_ns} ns");
            }
            // Medians over rounds: every round covers the same anchors, so
            // its value already is a mean per application, and one batch
            // hit by an interrupt (hundreds of microseconds in a batch of
            // tens) must not decide the state's time.
            let value = OperatorTime {
                batched_ns: super::median(&times),
                per_call_ns,
                restore_ns: super::median(&restores),
            };
            Ok((op, value))
        })
        .collect()
}

fn read_timing_parts(
    directory: &Path,
    key: &str,
) -> anyhow::Result<(
    arrow::record_batch::RecordBatch,
    arrow::record_batch::RecordBatch,
)> {
    if !has_part(directory, "batches", key) {
        bail!("{key} was timed without batched passes (an older run); run its times stage again");
    }
    let batches = read(&parts_directory(directory, "batches").join(format!("{key}.parquet")))?;
    let times = read(&parts_directory(directory, "timings").join(format!("{key}.parquet")))?;
    Ok((batches, times))
}

/// Both passes' times of every operator in the state `key` of a
/// measurement run, for later experiments that fit costs on per-call
/// times.
///
/// # Errors
///
/// If the state was not timed, or timed by a build without batched passes.
pub fn operator_times(
    directory: &Path,
    key: &str,
) -> anyhow::Result<HashMap<String, OperatorTime>> {
    let (batches, times) = read_timing_parts(directory, key)?;
    operator_times_from(key, &batches, &times)
}

#[derive(Default)]
struct CellRows {
    instance: Vec<String>,
    family: Vec<String>,
    n: Vec<u32>,
    seed: Vec<u32>,
    state: Vec<String>,
    length: Vec<i64>,
    gap: Vec<Option<f64>>,
    operator: Vec<String>,
    id: Vec<String>,
    op_family: Vec<String>,
    deterministic: Vec<bool>,
    rate: Vec<f64>,
    rate_low: Vec<f64>,
    rate_high: Vec<f64>,
    mean_gain: Vec<f64>,
    mean_time_ns: Vec<f64>,
    per_call_time_ns: Vec<f64>,
    per_call_over_batched: Vec<f64>,
    restore_ns: Vec<f64>,
    p_improve: Vec<f64>,
    improving_anchors: Vec<u32>,
    anchors: Vec<u32>,
    sampled: Vec<bool>,
    class: Vec<String>,
    improves_o7: Vec<bool>,
    improves_o5: Vec<bool>,
    improves_oropt: Vec<bool>,
    improves_3opt: Vec<bool>,
    improves_lk: Vec<bool>,
    gain_applications: Vec<u32>,
    timed_applications: Vec<u32>,
    timed_rounds: Vec<u32>,
    stop_reason: Vec<String>,
    timing_seconds: Vec<f64>,
    suspect: Vec<bool>,
    layout: Vec<String>,
}

impl CellRows {
    fn into_table(self) -> Table {
        Table::new()
            .with("instance", Column::Str(self.instance))
            .with("family", Column::Str(self.family))
            .with("n", Column::U32(self.n))
            .with("seed", Column::U32(self.seed))
            .with("state", Column::Str(self.state))
            .with("length", Column::I64(self.length))
            .with("gap", Column::OptF64(self.gap))
            .with("operator", Column::Str(self.operator))
            .with("operator_id", Column::Str(self.id))
            .with("operator_family", Column::Str(self.op_family))
            .with("deterministic", Column::Bool(self.deterministic))
            .with("rate", Column::F64(self.rate))
            .with("rate_low", Column::F64(self.rate_low))
            .with("rate_high", Column::F64(self.rate_high))
            .with("mean_gain", Column::F64(self.mean_gain))
            .with("mean_time_ns", Column::F64(self.mean_time_ns))
            .with("per_call_time_ns", Column::F64(self.per_call_time_ns))
            .with(
                "per_call_over_batched",
                Column::F64(self.per_call_over_batched),
            )
            .with("restore_ns", Column::F64(self.restore_ns))
            .with("p_improve", Column::F64(self.p_improve))
            .with("improving_anchors", Column::U32(self.improving_anchors))
            .with("anchors", Column::U32(self.anchors))
            .with("sampled", Column::Bool(self.sampled))
            .with("class", Column::Str(self.class))
            .with("improves_o7", Column::Bool(self.improves_o7))
            .with("improves_o5", Column::Bool(self.improves_o5))
            .with("improves_oropt", Column::Bool(self.improves_oropt))
            .with("improves_3opt", Column::Bool(self.improves_3opt))
            .with("improves_lk", Column::Bool(self.improves_lk))
            .with("gain_applications", Column::U32(self.gain_applications))
            .with("timed_applications", Column::U32(self.timed_applications))
            .with("timed_rounds", Column::U32(self.timed_rounds))
            .with("stop_reason", Column::Str(self.stop_reason))
            .with("timing_seconds", Column::F64(self.timing_seconds))
            .with("suspect", Column::Bool(self.suspect))
            .with("layout", Column::Str(self.layout))
    }
}

/// Cells and nodes of one state.
fn summarise_state(
    directory: &Path,
    settings: &Settings,
    task: &StateTask,
    optimum: Option<f64>,
) -> anyhow::Result<(CellRows, Table)> {
    let key = task.key();
    let meta: GainMeta = serde_json::from_str(&std::fs::read_to_string(meta_path(
        directory, "gains", &key,
    ))?)?;
    let timing: TimingMeta = serde_json::from_str(&std::fs::read_to_string(meta_path(
        directory, "timings", &key,
    ))?)?;
    let gains = read(&parts_directory(directory, "gains").join(format!("{key}.parquet")))?;
    let (batches, times) = read_timing_parts(directory, &key)?;
    let operator_times = operator_times_from(&key, &batches, &times)?;
    let mut gain_entries: HashMap<String, Vec<(City, u32, f64)>> = HashMap::new();
    for (((op, anchor), repeat), gain) in column_str(&gains, "operator")?
        .into_iter()
        .zip(column_i64(&gains, "anchor")?)
        .zip(column_i64(&gains, "repeat")?)
        .zip(column_i64(&gains, "gain")?)
    {
        gain_entries
            .entry(op)
            .or_default()
            .push((anchor as City, repeat as u32, gain as f64));
    }
    let mut time_entries: HashMap<String, Vec<(City, u32, f64)>> = HashMap::new();
    for (((op, anchor), round), time) in column_str(&times, "operator")?
        .into_iter()
        .zip(column_i64(&times, "anchor")?)
        .zip(column_i64(&times, "round")?)
        .zip(column_f64(&times, "time_ns")?)
    {
        time_entries
            .entry(op)
            .or_default()
            .push((anchor as City, round as u32, time));
    }
    let class = CLASSES[meta.flags.class()];
    let mut cells = CellRows::default();
    let mut node_op = Vec::new();
    let mut node_anchor = Vec::new();
    let mut node_gain = Vec::new();
    let mut node_time = Vec::new();
    let mut node_per_call = Vec::new();
    for op in operators() {
        let gains = gain_entries
            .get(op.name())
            .with_context(|| format!("{key}: no gains of {}", op.name()))?;
        let per_call = time_entries
            .get(op.name())
            .with_context(|| format!("{key}: no timings of {}", op.name()))?;
        let time = *operator_times
            .get(op.name())
            .with_context(|| format!("{key}: no batched times of {}", op.name()))?;
        // Per-call times carry the spread over anchors; scaled to the
        // batched mean, which is the operator's time.
        let scale = time.scale();
        let times: Vec<(City, u32, f64)> = per_call
            .iter()
            .map(|&(anchor, round, value)| (anchor, round, value * scale))
            .collect();
        let mut rng = Stream::new(settings.seed, "bootstrap", &[label_hash(&key), op as u64]);
        let summary = summarise_operator(
            &meta.anchors,
            gains,
            &times,
            meta.sampled,
            settings.bootstrap,
            &mut rng,
        )
        .with_context(|| format!("{key}: {}", op.name()))?;
        let gain_grid = Grid::build(&meta.anchors, gains)?;
        let time_grid = Grid::build(&meta.anchors, per_call)?;
        for ((&anchor, gain), time) in meta
            .anchors
            .iter()
            .zip(gain_grid.row_means())
            .zip(time_grid.row_means())
        {
            node_op.push(op.name().to_string());
            node_anchor.push(anchor);
            node_gain.push(gain);
            node_time.push(time * scale);
            node_per_call.push(time);
        }
        cells.instance.push(task.instance.clone());
        cells
            .family
            .push(instance_family(&task.instance).to_string());
        cells.n.push(task.n as u32);
        cells.seed.push(task.seed as u32);
        cells.state.push(task.state.name.clone());
        cells.length.push(meta.length);
        cells
            .gap
            .push(optimum.map(|optimum| (meta.length as f64 - optimum) / optimum));
        cells.operator.push(op.name().to_string());
        cells.id.push(op.id());
        cells.op_family.push(op.family().to_string());
        cells.deterministic.push(op.deterministic());
        cells.rate.push(summary.rate);
        cells.rate_low.push(summary.low);
        cells.rate_high.push(summary.high);
        cells.mean_gain.push(summary.mean_gain);
        cells.mean_time_ns.push(summary.mean_time_ns);
        cells.per_call_time_ns.push(time.per_call_ns);
        cells
            .per_call_over_batched
            .push(time.per_call_ns / time.batched_ns);
        cells.restore_ns.push(time.restore_ns);
        cells.p_improve.push(summary.p_improve);
        cells
            .improving_anchors
            .push(summary.improving_anchors as u32);
        cells.anchors.push(meta.anchors.len() as u32);
        cells.sampled.push(meta.sampled);
        cells.class.push(class.to_string());
        cells.improves_o7.push(meta.flags.o7);
        cells.improves_o5.push(meta.flags.o5);
        cells.improves_oropt.push(meta.flags.oropt);
        cells.improves_3opt.push(meta.flags.three_opt);
        cells.improves_lk.push(meta.flags.lk);
        cells.gain_applications.push(gains.len() as u32);
        cells.timed_applications.push(per_call.len() as u32);
        cells.timed_rounds.push(timing.rounds as u32);
        cells.stop_reason.push(timing.stop_reason.clone());
        cells.timing_seconds.push(timing.seconds);
        cells.suspect.push(timing.suspect);
        cells.layout.push(meta.layout.clone());
    }
    let nodes = Table::new()
        .with("operator", Column::Str(node_op))
        .with("anchor", Column::U32(node_anchor))
        .with("mean_gain", Column::F64(node_gain))
        .with("mean_time_ns", Column::F64(node_time))
        .with("per_call_time_ns", Column::F64(node_per_call));
    Ok((cells, nodes))
}

/// Leader per state: best and second-best operator by rate, whether their
/// intervals separate, the class, and the leader per application (best
/// mean gain, Claim 1.5).
fn leaders_table(cells: &CellRows) -> Table {
    let mut groups: Vec<(String, u32, String, Vec<usize>)> = Vec::new();
    let mut index_of: HashMap<(String, u32, String), usize> = HashMap::new();
    for row in 0..cells.instance.len() {
        let key = (
            cells.instance[row].clone(),
            cells.seed[row],
            cells.state[row].clone(),
        );
        let slot = *index_of.entry(key.clone()).or_insert_with(|| {
            groups.push((key.0, key.1, key.2, Vec::new()));
            groups.len() - 1
        });
        groups[slot].3.push(row);
    }
    let mut instance = Vec::new();
    let mut family = Vec::new();
    let mut n = Vec::new();
    let mut seed = Vec::new();
    let mut state = Vec::new();
    let mut class = Vec::new();
    let mut leader = Vec::new();
    let mut leader_rate = Vec::new();
    let mut rival = Vec::new();
    let mut rival_rate = Vec::new();
    let mut separated = Vec::new();
    let mut any_improvement = Vec::new();
    let mut leader_per_application = Vec::new();
    let mut o7_optimal_o5_improves = Vec::new();
    for (name, state_seed, state_name, rows) in groups {
        let mut ranked = rows.clone();
        ranked.sort_by(|&a, &b| cells.rate[b].total_cmp(&cells.rate[a]).then(a.cmp(&b)));
        let (top, second) = (ranked[0], ranked[1]);
        let per_application = *rows
            .iter()
            .max_by(|&&a, &&b| {
                cells.mean_gain[a]
                    .total_cmp(&cells.mean_gain[b])
                    .then(b.cmp(&a))
            })
            .expect("a state has operators");
        instance.push(name);
        family.push(cells.family[top].clone());
        n.push(cells.n[top]);
        seed.push(state_seed);
        state.push(state_name);
        class.push(cells.class[top].clone());
        leader.push(cells.operator[top].clone());
        leader_rate.push(cells.rate[top]);
        rival.push(cells.operator[second].clone());
        rival_rate.push(cells.rate[second]);
        separated.push(cells.rate_low[top] > cells.rate_high[second]);
        any_improvement.push(cells.rate[top] > 0.0);
        leader_per_application.push(cells.operator[per_application].clone());
        o7_optimal_o5_improves.push(!cells.improves_o7[top] && cells.improves_o5[top]);
    }
    Table::new()
        .with("instance", Column::Str(instance))
        .with("family", Column::Str(family))
        .with("n", Column::U32(n))
        .with("seed", Column::U32(seed))
        .with("state", Column::Str(state))
        .with("class", Column::Str(class))
        .with("leader", Column::Str(leader))
        .with("leader_rate", Column::F64(leader_rate))
        .with("rival", Column::Str(rival))
        .with("rival_rate", Column::F64(rival_rate))
        .with("separated", Column::Bool(separated))
        .with("any_improvement", Column::Bool(any_improvement))
        .with(
            "leader_per_application",
            Column::Str(leader_per_application),
        )
        .with(
            "o7_optimal_o5_improves",
            Column::Bool(o7_optimal_o5_improves),
        )
}

/// Times per (instance, operator) averaged over the instance's states:
/// the batched and per-call means and their ratio.  One operator's time
/// differs several-fold between states, so these averages are a check on
/// the two passes, never a substitute for the per-state times in a rate.
fn operator_time_table(cells: &CellRows) -> Table {
    let mut groups: std::collections::BTreeMap<(String, String), (f64, f64, u32)> =
        std::collections::BTreeMap::new();
    let mut ratios: std::collections::BTreeMap<String, Vec<f64>> =
        std::collections::BTreeMap::new();
    for row in 0..cells.instance.len() {
        let entry = groups
            .entry((cells.instance[row].clone(), cells.operator[row].clone()))
            .or_default();
        entry.0 += cells.mean_time_ns[row];
        entry.1 += cells.per_call_time_ns[row];
        entry.2 += 1;
        ratios
            .entry(cells.operator[row].clone())
            .or_default()
            .push(cells.per_call_over_batched[row]);
    }
    for (op, values) in &ratios {
        log::info!(
            "per-call / batched time, {op}: median {:.3}, 5% {:.3}, 95% {:.3} over {} states",
            super::median(values),
            quantile(values, 0.05),
            quantile(values, 0.95),
            values.len()
        );
    }
    let (mut instance, mut operator, mut states, mut batched, mut per_call, mut ratio) =
        (vec![], vec![], vec![], vec![], vec![], vec![]);
    for ((name, op), (batched_sum, per_call_sum, count)) in groups {
        instance.push(name);
        operator.push(op);
        states.push(count);
        batched.push(batched_sum / f64::from(count));
        per_call.push(per_call_sum / f64::from(count));
        ratio.push(per_call_sum / batched_sum);
    }
    Table::new()
        .with("instance", Column::Str(instance))
        .with("operator", Column::Str(operator))
        .with("states", Column::U32(states))
        .with("mean_batched_ns", Column::F64(batched))
        .with("mean_per_call_ns", Column::F64(per_call))
        .with("per_call_over_batched", Column::F64(ratio))
}

/// Stage 3: cells, nodes and leaders of every timed state.
///
/// # Errors
///
/// If a state lacks a pass, or a summary fails.
pub fn run_summarise(
    directory: &Path,
    settings: &Settings,
    tasks: &[StateTask],
    optima: &HashMap<String, Option<f64>>,
    workers: usize,
) -> anyhow::Result<()> {
    if let Some(missing) = tasks
        .iter()
        .find(|task| !has_part(directory, "timings", &task.key()))
    {
        bail!(
            "{} has no timings yet; run the times stage first",
            missing.label()
        );
    }
    log::info!("summarise: {} states on {workers} threads", tasks.len());
    let results = parallel_map(
        tasks,
        workers,
        |task| task.label(),
        |task| {
            let (cells, nodes) =
                summarise_state(directory, settings, task, optima[&task.instance])?;
            write_part(directory, "nodes", &task.key(), nodes)?;
            Ok(cells)
        },
    )?;
    let mut cells = CellRows::default();
    for part in results {
        macro_rules! extend {
            ($($field:ident),*) => { $(cells.$field.extend(part.$field);)* };
        }
        extend!(
            instance,
            family,
            n,
            seed,
            state,
            length,
            gap,
            operator,
            id,
            op_family,
            deterministic,
            rate,
            rate_low,
            rate_high,
            mean_gain,
            mean_time_ns,
            per_call_time_ns,
            per_call_over_batched,
            restore_ns,
            p_improve,
            improving_anchors,
            anchors,
            sampled,
            class,
            improves_o7,
            improves_o5,
            improves_oropt,
            improves_3opt,
            improves_lk,
            gain_applications,
            timed_applications,
            timed_rounds,
            stop_reason,
            timing_seconds,
            suspect,
            layout
        );
    }
    let leaders = leaders_table(&cells);
    let states = cells.instance.len() / operators().len();
    write(&directory.join("leaders.parquet"), leaders)?;
    write(
        &directory.join("operator_times.parquet"),
        operator_time_table(&cells),
    )?;
    write(&directory.join("cells.parquet"), cells.into_table())?;
    log::info!(
        "wrote cells.parquet, leaders.parquet and operator_times.parquet ({states} states) to {}",
        directory.display()
    );
    Ok(())
}

/// Which stages to run.
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum Stage {
    /// Pass 1 only.
    Gains,
    /// Pass 2 only.
    Times,
    /// Summaries only.
    Summarise,
    /// All three, in order.
    All,
}

/// Runs the requested stages in `directory` (created on first use).
///
/// # Errors
///
/// If the directory was started with other settings, or a stage fails.
#[allow(clippy::too_many_arguments)]
pub fn run(
    repository: &Path,
    directory: &Path,
    settings: &Settings,
    stage: Stage,
    gain_workers: usize,
    timed_workers: usize,
    allow_unlocked: bool,
) -> anyhow::Result<()> {
    std::fs::create_dir_all(directory)?;
    let config = directory.join("config.json");
    if config.is_file() {
        let previous: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&config)?)?;
        let previous: Settings =
            serde_json::from_value(previous["settings"]["measurement"].clone())
                .context("config.json of this run has no readable settings")?;
        if &previous != settings {
            bail!(
                "{} was started with other settings; every stage must use the same ones (see its config.json)",
                directory.display()
            );
        }
    } else {
        write_config(
            directory,
            repository,
            json!({
                "experiment": "measurement A (marginal rates)",
                "measurement": settings,
            }),
        )?;
    }
    let tasks = select_tasks(repository, settings)?;
    log::info!("{} states selected", tasks.len());
    let workers = if gain_workers == 0 {
        all_cpus()
    } else {
        gain_workers
    };
    if matches!(stage, Stage::Gains | Stage::All) {
        run_gains(repository, directory, settings, &tasks, workers)?;
    }
    if matches!(stage, Stage::Times | Stage::All) {
        run_times(
            repository,
            directory,
            settings,
            &tasks,
            timed_workers,
            allow_unlocked,
        )?;
    }
    if matches!(stage, Stage::Summarise | Stage::All) {
        let files = read_states_directory(&resolve(repository, &settings.states))?;
        let optima: HashMap<String, Option<f64>> = files
            .iter()
            .map(|file| {
                let optimum = crate::instance::Instance::load(
                    &repository.join(&file.path),
                    &repository.join(crate::instance::alpha::CACHE_DIRECTORY),
                )
                .map(|instance| instance.optimum.map(|value| value as f64));
                optimum.map(|optimum| (file.instance.clone(), optimum))
            })
            .collect::<anyhow::Result<_>>()?;
        run_summarise(directory, settings, &tasks, &optima, workers)?;
    }
    Ok(())
}

/// Rates per (instance, seed, state), by operator name.
pub type RateMap = HashMap<(String, u32, String), HashMap<String, f64>>;

/// Reads a cells table's (instance, seed, state, operator) → rate map, for
/// later experiments.
///
/// # Errors
///
/// If the table cannot be read.
pub fn read_rates(cells: &Path) -> anyhow::Result<RateMap> {
    let batch = read(cells)?;
    let mut rates: RateMap = HashMap::new();
    for (((instance, seed), state), (operator, rate)) in column_str(&batch, "instance")?
        .into_iter()
        .zip(column_i64(&batch, "seed")?)
        .zip(column_str(&batch, "state")?)
        .zip(
            column_str(&batch, "operator")?
                .into_iter()
                .zip(column_f64(&batch, "rate")?),
        )
    {
        rates
            .entry((instance, seed as u32, state))
            .or_default()
            .insert(operator, rate);
    }
    Ok(rates)
}

/// Whether a table column holds `true` for a row (re-export for callers
/// that read leaders tables).
///
/// # Errors
///
/// If the column is missing.
pub fn read_bool(
    batch: &arrow::record_batch::RecordBatch,
    name: &str,
) -> anyhow::Result<Vec<bool>> {
    column_bool(batch, name)
}
