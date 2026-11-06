//! What several experiments share: the states file format, instance
//! families, the dispatch from run options to monomorphised operator code,
//! an untimed parallel map, and batched timing.

use std::path::{Path, PathBuf};

use anyhow::{Context, bail};
use serde::{Deserialize, Serialize};

use crate::City;
use crate::instance::Instance;
use crate::instance::alpha::CACHE_DIRECTORY;
use crate::instance::storage::{Computed, Dist, Matrix};
use crate::ops::{Op, Problem, Work};
use crate::rng::Stream;
use crate::timing::counters::{self, Counts};
use crate::timing::disturb;
use crate::timing::tsc::{self, Clock};
use crate::tour::array::Array;
use crate::tour::blocks::Blocks;
use crate::tour::{Layout, Replay, Tour};

/// Local-optimality classes in order of search progress (the labels of the
/// Python analysis, `aos.analysis.e1.CLASSES`).
pub const CLASSES: [&str; 4] = ["2-opt open", "2-opt optimum", "3-opt optimum", "LK optimum"];

/// The state names of the E1 builder, in search order.
pub const STATE_NAMES: [&str; 12] = [
    "random",
    "nearest_neighbour",
    "d25",
    "d50",
    "d75",
    "d90",
    "opt2",
    "oropt",
    "opt3",
    "lkopt",
    "ils1",
    "ils2",
];

/// States with many defects, which the budgeted runs skip on large
/// instances (kicks and descents there run over the whole tour).
pub const EARLY_STATES: [&str; 2] = ["random", "nearest_neighbour"];

/// Position of a state name in [`STATE_NAMES`] (unknown names last).
pub fn state_rank(name: &str) -> usize {
    STATE_NAMES
        .iter()
        .position(|state| *state == name)
        .unwrap_or(STATE_NAMES.len())
}

/// Family of an instance: its generator's, or `TSPLIB` for any other name
/// (`aos.analysis.e1_marginal.instance_family`).
pub fn instance_family(name: &str) -> &'static str {
    for (family, prefix) in [
        ("uniform", "portgen-"),
        ("clustered", "netgen-"),
        ("tspgen", "tspgen-"),
        ("random matrix", "portmgen-"),
    ] {
        if name.starts_with(prefix) {
            return family;
        }
    }
    "TSPLIB"
}

/// One state of a states file (the format of `aos/e1_states.py`, which
/// the Rust builder also writes, so both sources load the same way).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateRecord {
    /// State name (see [`STATE_NAMES`]).
    pub name: String,
    /// Gap to the optimum when known (as written; recomputed on load).
    pub gap: Option<f64>,
    /// Length as written by the builder (recomputed on load: Python's GEO
    /// distances differ, `ideas/rust-port.md` §4.1).
    pub length: f64,
    /// How the state was made.
    pub source: String,
    /// The tour.
    pub order: Vec<City>,
    /// Name of an earlier identical state, if any (such states are skipped).
    pub duplicate_of: Option<String>,
}

/// The states of one (instance, seed).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StatesFile {
    /// Instance name.
    pub instance: String,
    /// Instance path relative to the repository root.
    pub path: String,
    /// Number of cities.
    pub n: usize,
    /// Optimal or best-known length, when known.
    pub optimum: Option<f64>,
    /// State seed.
    pub state_seed: u64,
    /// The states, in building order.
    pub states: Vec<StateRecord>,
}

/// Reads every `*.json` states file of `directory`, sorted by file name.
///
/// # Errors
///
/// If the directory holds no states files or one cannot be parsed.
pub fn read_states_directory(directory: &Path) -> anyhow::Result<Vec<StatesFile>> {
    let mut paths: Vec<PathBuf> = std::fs::read_dir(directory)
        .with_context(|| format!("reading {}", directory.display()))?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.extension()
                .is_some_and(|extension| extension == "json")
        })
        .collect();
    paths.sort();
    if paths.is_empty() {
        bail!("no states files in {}", directory.display());
    }
    paths
        .iter()
        .map(|path| {
            let text = std::fs::read_to_string(path)
                .with_context(|| format!("reading {}", path.display()))?;
            serde_json::from_str(&text).with_context(|| format!("parsing {}", path.display()))
        })
        .collect()
}

/// Loads an instance by its repository-relative path, with alpha
/// candidates (computed and cached when missing).
///
/// # Errors
///
/// If the file cannot be loaded or the cache written.
pub fn load_instance(repository: &Path, path: &str) -> anyhow::Result<Instance> {
    let cache = repository.join(CACHE_DIRECTORY);
    let mut instance = Instance::load(&repository.join(path), &cache)?;
    instance.ensure_alpha(&cache)?;
    Ok(instance)
}

/// Distance storage of a run (`ideas/rust-port.md` §4.2, §9).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, clap::ValueEnum)]
#[serde(rename_all = "snake_case")]
pub enum Storage {
    /// The dense matrix (main design).
    Matrix,
    /// Computed from coordinates on every read (robustness factor).
    Computed,
}

/// Tour representation of a run (`ideas/rust-port.md` §5.3, §9).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, clap::ValueEnum)]
#[serde(rename_all = "snake_case")]
pub enum LayoutChoice {
    /// The array below `n_split` cities, the two-level list from it on.
    Auto,
    /// Always the array.
    Array,
    /// Always the two-level list.
    TwoLevel,
}

/// The implementation choices a run fixes (main design or one robustness
/// factor changed).
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Implementation {
    /// Distance storage.
    pub storage: Storage,
    /// Whether (city, candidate) distances come from the candidate cache.
    pub cache: bool,
    /// Tour representation.
    pub layout: LayoutChoice,
    /// Threshold of [`LayoutChoice::Auto`].
    pub n_split: usize,
}

impl Default for Implementation {
    fn default() -> Self {
        Self {
            storage: Storage::Matrix,
            cache: true,
            layout: LayoutChoice::Auto,
            n_split: 1000,
        }
    }
}

impl Implementation {
    /// Whether an instance of `n` cities uses the two-level list.
    pub fn two_level(&self, n: usize) -> bool {
        match self.layout {
            LayoutChoice::Auto => n >= self.n_split,
            LayoutChoice::Array => false,
            LayoutChoice::TwoLevel => true,
        }
    }

    /// The layout name used on an instance of `n` cities.
    pub fn layout_name(&self, n: usize) -> &'static str {
        if self.two_level(n) {
            Blocks::NAME
        } else {
            Array::NAME
        }
    }
}

/// Work that runs on one instance with any storage, cache mode and layout;
/// [`dispatch`] picks the monomorphised version the run options ask for.
pub trait Job {
    /// What the job returns.
    type Output;

    /// Runs the job.  `dist` is the storage the operators read (the same
    /// object as `problem.dist`).
    fn run<D: Dist, const C: bool, L: Layout>(
        self,
        instance: &Instance,
        problem: &Problem<'_, D, C>,
    ) -> anyhow::Result<Self::Output>;
}

fn with_layout<J: Job, D: Dist, const C: bool>(
    job: J,
    instance: &Instance,
    problem: &Problem<'_, D, C>,
    two_level: bool,
) -> anyhow::Result<J::Output> {
    if two_level {
        job.run::<D, C, Blocks>(instance, problem)
    } else {
        job.run::<D, C, Array>(instance, problem)
    }
}

fn with_cache<J: Job, D: Dist>(
    job: J,
    instance: &Instance,
    dist: &D,
    cache: bool,
    two_level: bool,
) -> anyhow::Result<J::Output> {
    if cache {
        let problem: Problem<'_, D, true> = Problem {
            dist,
            neighbours: &instance.neighbours,
            alpha: instance.alpha.as_ref(),
        };
        with_layout(job, instance, &problem, two_level)
    } else {
        let problem: Problem<'_, D, false> = Problem {
            dist,
            neighbours: &instance.neighbours,
            alpha: instance.alpha.as_ref(),
        };
        with_layout(job, instance, &problem, two_level)
    }
}

/// Runs `job` with the storage, cache mode and layout `implementation`
/// asks for on `instance`.
///
/// # Errors
///
/// If computed storage is asked for an instance without coordinates, or
/// the job fails.
pub fn dispatch<J: Job>(
    job: J,
    instance: &Instance,
    implementation: &Implementation,
) -> anyhow::Result<J::Output> {
    let two_level = implementation.two_level(instance.n());
    match implementation.storage {
        Storage::Matrix => with_cache::<J, Matrix>(
            job,
            instance,
            &instance.matrix,
            implementation.cache,
            two_level,
        ),
        Storage::Computed => {
            let computed: &Computed = instance.computed.as_ref().with_context(|| {
                format!("{} has no coordinates for computed storage", instance.name)
            })?;
            with_cache::<J, Computed>(job, instance, computed, implementation.cache, two_level)
        }
    }
}

/// A tour of `order` in layout `L`, its length computed from the matrix
/// (every storage gives identical distances).
pub fn tour_of<L: Layout>(instance: &Instance, order: &[City]) -> Tour<L> {
    Tour::new(order, &instance.matrix)
}

/// Applies `job` to every item on a `rayon` pool of `workers` threads
/// (untimed work only) and returns the results in item order.  An error
/// stops the run: no new items start, the running ones finish, and one of
/// the errors (which one, when several items fail, is unspecified) is
/// returned.
///
/// # Errors
///
/// An error an item returned, naming the item, or the pool's if it
/// cannot be built.
pub fn parallel_map<T: Sync, R: Send>(
    items: &[T],
    workers: usize,
    name: impl Fn(&T) -> String + Sync,
    job: impl Fn(&T) -> anyhow::Result<R> + Sync,
) -> anyhow::Result<Vec<R>> {
    use rayon::prelude::*;
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(workers.clamp(1, items.len().max(1)))
        .build()
        .context("building the worker pool")?;
    pool.install(|| {
        items
            .par_iter()
            .map(|item| job(item).with_context(|| name(item)))
            .collect()
    })
}

/// Every logical CPU this process may use.
pub fn all_cpus() -> usize {
    std::thread::available_parallelism().map_or(1, usize::from)
}

// ------------------------------------------------------- batched timing

/// The timed passes over one operator's anchors (`ideas/rust-port.md`
/// §7.4).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Pass {
    /// One timed region per application (per-anchor times).
    PerCall,
    /// One timed region around every anchor, each application restored to
    /// the state inside the region.
    Batch,
    /// One timed region around replaying every improving application's
    /// changes and restoring them: about two restores each, without the
    /// operator's search.
    Restore,
}

impl Pass {
    /// The passes of one operator over its anchors in an order drawn from
    /// `rng`, so that no pass always finds the caches warmed by another:
    /// per call and batched always, the restore batch only when some
    /// application improves (otherwise nothing is restored).
    pub fn order(improves: bool, rng: &mut Stream) -> Vec<Pass> {
        let mut passes = vec![Pass::PerCall, Pass::Batch];
        if improves {
            passes.push(Pass::Restore);
        }
        rng.shuffle(&mut passes);
        passes
    }
}

/// Applies `op` at every anchor once, untimed, each application from its
/// own stream from `make_stream` and undone: run before an operator's timed
/// passes so that each of them finds the operator's code and data in
/// cache, as a search does when it runs one operator back to back during a
/// slice.  Without it the first timed pass of an operator (they run in a
/// random order) ran cold and the others warm: E0's batched times then
/// differed up to fourfold between repeats on large instances.
pub fn warm_up<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    op: Op,
    anchors: &[City],
    make_stream: impl Fn(City) -> Stream,
) {
    let mut work = Work::default();
    for &anchor in anchors {
        let mark = tour.mark();
        let mut rng = make_stream(anchor);
        op.apply(tour, problem, &mut rng, &mut work, Some(anchor));
        tour.undo_to(mark);
    }
}

/// One timed region around applying `op` at every anchor in turn, each
/// application from its own stream in `streams` and restored to the state
/// inside the region.  Writes each application's length change to
/// `deltas` and returns the region's time and counts; the caller checks the
/// changes outside the region.
///
/// # Panics
///
/// If `streams` or `deltas` do not have one entry per anchor.
#[allow(clippy::too_many_arguments)]
pub fn timed_batch<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    op: Op,
    anchors: &[City],
    streams: &mut [Stream],
    clock: &Clock,
    deltas: &mut [i64],
) -> Sample {
    assert_eq!(streams.len(), anchors.len(), "one stream per anchor");
    assert_eq!(deltas.len(), anchors.len(), "one delta per anchor");
    let mut work = Work::default();
    let counted = counters::read();
    let begin = tsc::start();
    for ((rng, &anchor), delta) in streams.iter_mut().zip(anchors).zip(deltas.iter_mut()) {
        let mark = tour.mark();
        *delta = op.apply(tour, problem, rng, &mut work, Some(anchor)).delta;
        tour.undo_to(mark);
    }
    let end = tsc::stop();
    Sample {
        ns: clock.region_ns(begin, end),
        counts: counters::between(counted, counters::read()),
    }
}

/// One timing of a region: its nanoseconds and, where the performance
/// counters can be read, its user-space instructions and cycles
/// ([`counters`]).
#[derive(Debug, Clone, Copy)]
pub struct Sample {
    /// Wall time of the region.
    pub ns: f64,
    /// Instructions and cycles of the region.
    pub counts: Option<Counts>,
}

/// A timed region shorter than this (ns) is timed again (see [`sampled`]).
pub const SAMPLE_TARGET_NS: f64 = 1_000_000.0;
/// Fewest samples of a region shorter than [`SAMPLE_TARGET_NS`].
pub const MIN_SAMPLES: usize = 3;
/// Most samples of a region.
pub const MAX_SAMPLES: usize = 9;
/// Most retakes of a disturbed set of samples (then it is kept as it is).
pub const MAX_RETAKES: usize = 10;

/// The median of repeated timings of one region, `region` timing it once.
/// A region of at least [`SAMPLE_TARGET_NS`] is timed once: an interrupt
/// adds well under 1% to it.  A shorter one is timed at least
/// [`MIN_SAMPLES`] times, and again until the samples add up to the target
/// or [`MAX_SAMPLES`] is reached.  A batch of a cheap operator lasts a few
/// microseconds, so a single interrupt or cache disturbance can double one
/// timing of it; E0 measured such outliers in about one repeat in five,
/// and the median of the samples removes them (repeats then agree within
/// 0.5%).
///
/// When another task disturbed the samples (it preempted the thread or ran
/// on its SMT sibling, [`disturb`]), all of them are taken again, up to
/// [`MAX_RETAKES`] times; the retakes are counted
/// ([`disturb::take_retakes`]).  The check reads `/proc/schedstat`, so it
/// is made once around the whole set of samples: between samples, the read
/// itself cooled the caches and spread the samples out.
pub fn sampled(mut region: impl FnMut() -> Sample) -> Sample {
    let mut retakes = 0;
    loop {
        let before = disturb::with_watch(|watch| watch.now());
        let mut samples = vec![region()];
        let mut total = samples[0].ns;
        if total < SAMPLE_TARGET_NS {
            while samples.len() < MIN_SAMPLES
                || (total < SAMPLE_TARGET_NS && samples.len() < MAX_SAMPLES)
            {
                let sample = region();
                total += sample.ns;
                samples.push(sample);
            }
        }
        let disturbed = disturb::with_watch(|watch| watch.disturbed(before, total));
        if !disturbed || retakes == MAX_RETAKES {
            return median_of(samples);
        }
        retakes += 1;
        disturb::count_retake();
    }
}

/// The median sample by time, with that sample's counts (the mean of the
/// middle two for an even count).
fn median_of(mut samples: Vec<Sample>) -> Sample {
    samples.sort_by(|a, b| a.ns.total_cmp(&b.ns));
    let middle = samples.len() / 2;
    if samples.len() % 2 == 1 {
        samples[middle]
    } else {
        let (low, high) = (samples[middle - 1], samples[middle]);
        Sample {
            ns: (low.ns + high.ns) / 2.0,
            counts: low.counts.zip(high.counts).map(|(a, b)| a.mean_with(b)),
        }
    }
}

/// [`timed_batch`] timed as [`sampled`] says, each sample from fresh
/// streams from `make_stream`; writes the length changes to `deltas`.
///
/// # Errors
///
/// If two samples change the length differently at some anchor (the
/// applications are not reproducible).
#[allow(clippy::too_many_arguments)]
pub fn sampled_batch<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    op: Op,
    anchors: &[City],
    make_stream: impl Fn(City) -> Stream,
    clock: &Clock,
    deltas: &mut [i64],
) -> anyhow::Result<Sample> {
    let mut first: Option<Vec<i64>> = None;
    let mut consistent = true;
    let elapsed = sampled(|| {
        tour.normalize();
        let mut streams: Vec<Stream> = anchors.iter().map(|&anchor| make_stream(anchor)).collect();
        let mut sample = vec![0i64; anchors.len()];
        let timed = timed_batch(tour, problem, op, anchors, &mut streams, clock, &mut sample);
        match &first {
            None => first = Some(sample),
            Some(previous) => consistent &= *previous == sample,
        }
        timed
    });
    if !consistent {
        bail!(
            "{}: batch samples changed the length differently",
            op.name()
        );
    }
    deltas.copy_from_slice(&first.expect("sampled times at least once"));
    Ok(elapsed)
}

/// The replays of every improving application among `anchors`, untimed:
/// what the restore batch replays.  Each application runs from its own
/// stream, from `make_stream`, and is undone.
pub fn improving_replays<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    op: Op,
    anchors: &[City],
    make_stream: impl Fn(City) -> Stream,
) -> Vec<Replay> {
    anchors
        .iter()
        .filter_map(|&anchor| {
            let mark = tour.mark();
            let mut rng = make_stream(anchor);
            let delta = op
                .apply(tour, problem, &mut rng, &mut Work::default(), Some(anchor))
                .delta;
            let replay = (delta < 0).then(|| tour.replay_since(mark));
            tour.undo_to(mark);
            replay
        })
        .collect()
}

/// One timed region around replaying each of `replays` and restoring the
/// state after each: about two restores per replay.  Returns the region's
/// nanoseconds.
pub fn timed_restores<L: Layout>(tour: &mut Tour<L>, replays: &[Replay], clock: &Clock) -> Sample {
    sampled(|| {
        tour.normalize();
        let counted = counters::read();
        let begin = tsc::start();
        for replay in replays {
            let mark = tour.mark();
            tour.replay(replay);
            tour.undo_to(mark);
        }
        let end = tsc::stop();
        Sample {
            ns: clock.region_ns(begin, end),
            counts: counters::between(counted, counters::read()),
        }
    })
}

/// The restore time of a batch and the operator's time per application:
/// `restore = restores / 2` (zero without a restore batch) and
/// `(single - restore) / applications`.
pub fn batched_time(single: f64, restores: Option<f64>, applications: usize) -> (f64, f64) {
    let restore = restores.map_or(0.0, |restores| restores / 2.0);
    (restore, (single - restore) / applications as f64)
}

/// The instructions and cycles per application of a batch, as
/// [`batched_time`] computes its time: half the restore batch's counts
/// removed, divided by `applications`.  `None` when a region was not
/// counted.
pub fn batched_counts(
    single: Sample,
    restores: Option<Sample>,
    applications: usize,
) -> Option<Counts> {
    let restore = match restores {
        None => Counts::default(),
        Some(restores) => restores.counts?.scaled(0.5),
    };
    Some(
        single
            .counts?
            .minus(restore)
            .scaled(1.0 / applications as f64),
    )
}
