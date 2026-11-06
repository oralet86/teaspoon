//! Population runs (`aos/slices.py`, `aos/population.py`;
//! `ideas/rust-port.md` §8.4): a steady-state population search with one
//! operator slice per step, for E2/E3 and the greedy-vs-softmax pilot.
//!
//! Each step picks a tour by tournament, copies it, gives the copy one
//! slice of the operator the arm chooses, and replaces the worst tour if
//! the copy is shorter and its length is not already present.  Four
//! streams derive from the seed (initial tours, selection, arm, operators),
//! so arms with the same seed start from the same population.
//!
//! A slice is bounded by wall-clock time (TSC deadline; the application
//! running at the deadline is finished and the overshoot logged) or by an
//! application count.  A time-bounded run logs every slice's operator and
//! application count, so it replays exactly in count mode; `--check-replay`
//! does that after every run and stops on any difference.
//!
//! Arms: `fixed:<operator>`, `uniform`, `pm` (probability matching), `ap`
//! (adaptive pursuit), and two arms over a measured rate table:
//! `greedy` (the best-rated operator of the tour's exact class) and
//! `softmax:<tau>` (operator i with probability ∝ (rate_i / max rate)^(1/tau),
//! i.e. a softmax on log rates).  Classifying the tour is reference
//! knowledge and is not charged to the budget.

use std::collections::HashMap;
use std::path::Path;
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

use anyhow::{Context, bail};
use serde::{Deserialize, Serialize};
use serde_json::json;

use super::common::{CLASSES, Implementation, Job, dispatch, load_instance, tour_of};
use super::marginal::classify;
use crate::City;
use crate::instance::Instance;
use crate::instance::storage::Dist;
use crate::io::parquet::{
    Column, Table, column_f64, column_str, has_part, merge_parts, read, write_part,
};
use crate::io::provenance::write_config;
use crate::io::rundir::write_atomic;
use crate::ops::{Op, Problem, Work};
use crate::rng::Stream;
use crate::timing::machine;
use crate::timing::pin;
use crate::timing::tsc::{self, Clock};
use crate::tour::{Layout, Tour};

/// Default share of probability the adaptive arms' floors hold together.
pub const FLOOR_SHARE: f64 = 0.2;

/// How an arm is specified on the command line.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum ArmSpec {
    /// Always one operator.
    Fixed(String),
    /// Uniform over all operators.
    Uniform,
    /// Probability matching on the slice's gain per second.
    ProbabilityMatching,
    /// Adaptive pursuit on the slice's gain per second.
    AdaptivePursuit,
    /// The best-rated operator of the tour's class.
    Greedy,
    /// Softmax on log rates with temperature `tau`.
    Softmax(f64),
}

impl ArmSpec {
    /// Parses `fixed:<op>`, `uniform`, `pm`, `ap`, `greedy`, `softmax:<tau>`.
    ///
    /// # Errors
    ///
    /// On an unknown arm or operator, or a non-positive temperature.
    pub fn parse(text: &str) -> anyhow::Result<Self> {
        Ok(match text.split_once(':') {
            Some(("fixed", name)) => {
                if Op::parse_stored(name)?.is_none() {
                    bail!("operator {name} was retired on 2026-11-05");
                }
                Self::Fixed(name.to_string())
            }
            Some(("softmax", tau)) => {
                let tau: f64 = tau.parse().context("softmax temperature")?;
                if tau <= 0.0 {
                    bail!("softmax temperature must be positive");
                }
                Self::Softmax(tau)
            }
            None if text == "uniform" => Self::Uniform,
            None if text == "pm" => Self::ProbabilityMatching,
            None if text == "ap" => Self::AdaptivePursuit,
            None if text == "greedy" => Self::Greedy,
            _ => bail!("unknown arm {text:?}"),
        })
    }

    /// Name used in file names and tables.
    pub fn label(&self) -> String {
        match self {
            Self::Fixed(name) => format!("fixed-{name}"),
            Self::Uniform => "uniform".into(),
            Self::ProbabilityMatching => "pm".into(),
            Self::AdaptivePursuit => "ap".into(),
            Self::Greedy => "greedy".into(),
            Self::Softmax(tau) => format!("softmax-{tau}"),
        }
    }

    fn needs_class(&self) -> bool {
        matches!(self, Self::Greedy | Self::Softmax(_))
    }
}

/// What one slice did.
#[derive(Debug, Clone, Copy, Default)]
pub struct SliceRecord {
    /// Wall time of the slice, ns (overshoot included).
    pub duration_ns: f64,
    /// Time past the deadline taken by the last application.
    pub overshoot_ns: f64,
    /// Applications made.
    pub applications: u64,
    /// Improving applications.
    pub improving: u64,
    /// Length reduction.
    pub gain: i64,
    /// Work counted.
    pub work: Work,
    /// Anchor of the last improving application, or -1.
    pub anchor: i64,
}

/// The arm's state.
enum Arm {
    Fixed(Op),
    Uniform,
    Matching {
        quality: Vec<f64>,
        probabilities: Vec<f64>,
        minimum: f64,
    },
    Pursuit {
        quality: Vec<f64>,
        probabilities: Vec<f64>,
        minimum: f64,
        maximum: f64,
    },
    Greedy(Vec<Vec<f64>>),
    Softmax(Vec<Vec<f64>>, f64),
    Replay(Vec<(Op, u64)>, usize),
}

const LEARNING_RATE: f64 = 0.1;
const PURSUIT_RATE: f64 = 0.1;

fn draw(probabilities: &[f64], rng: &mut Stream) -> Op {
    // `rand`'s weighted sampling; until 2026-11-02 a hand-written
    // cumulative walk returned the last operator whenever rounding left
    // its target unspent, even when that operator's weight was zero.
    let weighted = rand::distr::weighted::WeightedIndex::new(probabilities)
        .expect("operator weights are finite, non-negative and not all zero");
    Op::ALL[rng.sample_from(&weighted)]
}

impl Arm {
    fn new(spec: &ArmSpec, table: Option<&Vec<Vec<f64>>>) -> anyhow::Result<Self> {
        let count = Op::ALL.len() as f64;
        Ok(match spec {
            ArmSpec::Fixed(name) => Self::Fixed(Op::from_name(name).context("operator")?),
            ArmSpec::Uniform => Self::Uniform,
            ArmSpec::ProbabilityMatching => Self::Matching {
                quality: vec![0.0; Op::ALL.len()],
                probabilities: vec![1.0 / count; Op::ALL.len()],
                minimum: FLOOR_SHARE / count,
            },
            ArmSpec::AdaptivePursuit => {
                let minimum = FLOOR_SHARE / count;
                Self::Pursuit {
                    quality: vec![0.0; Op::ALL.len()],
                    probabilities: vec![1.0 / count; Op::ALL.len()],
                    minimum,
                    maximum: 1.0 - (count - 1.0) * minimum,
                }
            }
            ArmSpec::Greedy => Self::Greedy(table.context("greedy needs --rates")?.clone()),
            ArmSpec::Softmax(tau) => {
                Self::Softmax(table.context("softmax needs --rates")?.clone(), *tau)
            }
        })
    }

    /// The operator for the next slice and, for replays, its count.
    fn next(&mut self, rng: &mut Stream, class: Option<usize>) -> (Op, Option<u64>) {
        match self {
            Self::Fixed(op) => (*op, None),
            Self::Uniform => (Op::ALL[rng.index(Op::ALL.len())], None),
            Self::Matching { probabilities, .. } | Self::Pursuit { probabilities, .. } => {
                (draw(probabilities, rng), None)
            }
            Self::Greedy(table) => {
                let rates = &table[class.expect("class given to level arms")];
                let best = (0..rates.len())
                    .max_by(|&a, &b| rates[a].total_cmp(&rates[b]).then(b.cmp(&a)))
                    .expect("operators exist");
                (Op::ALL[best], None)
            }
            Self::Softmax(table, tau) => {
                let rates = &table[class.expect("class given to level arms")];
                let top = rates.iter().copied().fold(0.0f64, f64::max);
                let weights: Vec<f64> = if top <= 0.0 {
                    vec![1.0; rates.len()]
                } else {
                    rates
                        .iter()
                        .map(|rate| {
                            if *rate > 0.0 {
                                (rate / top).powf(1.0 / *tau)
                            } else {
                                0.0
                            }
                        })
                        .collect()
                };
                (draw(&weights, rng), None)
            }
            Self::Replay(log, next) => {
                let (op, count) = log[*next];
                *next += 1;
                (op, Some(count))
            }
        }
    }

    fn update(&mut self, op: Op, record: &SliceRecord) {
        let reward = record.gain as f64 / record.duration_ns.max(1.0) * 1e9;
        match self {
            Self::Matching {
                quality,
                probabilities,
                minimum,
            } => {
                let index = op.index();
                quality[index] += LEARNING_RATE * (reward - quality[index]);
                let total: f64 = quality.iter().sum();
                let count = quality.len() as f64;
                if total <= 0.0 {
                    probabilities.fill(1.0 / count);
                } else {
                    let spread = 1.0 - count * *minimum;
                    for (probability, value) in probabilities.iter_mut().zip(quality.iter()) {
                        *probability = *minimum + spread * value / total;
                    }
                }
            }
            Self::Pursuit {
                quality,
                probabilities,
                minimum,
                maximum,
            } => {
                let index = op.index();
                quality[index] += LEARNING_RATE * (reward - quality[index]);
                let leader = (0..quality.len())
                    .max_by(|&a, &b| quality[a].total_cmp(&quality[b]).then(b.cmp(&a)))
                    .expect("operators exist");
                for (k, probability) in probabilities.iter_mut().enumerate() {
                    let target = if k == leader { *maximum } else { *minimum };
                    *probability += PURSUIT_RATE * (target - *probability);
                }
            }
            _ => {}
        }
    }
}

/// Run settings.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    /// Tours in the population.
    pub size: usize,
    /// Tours compared when picking the tour to work on.
    pub tournament: usize,
    /// Wall-clock length of a slice.
    pub slice_seconds: Option<f64>,
    /// Applications per slice (count mode).
    pub slice_applications: Option<u64>,
    /// Wall-clock budget of the run.
    pub budget_seconds: Option<f64>,
    /// Total applications allowed.
    pub budget_applications: Option<u64>,
    /// Slices allowed.
    pub budget_slices: Option<u64>,
}

impl Config {
    /// Checks the settings.
    ///
    /// # Errors
    ///
    /// Describing the inconsistency.
    pub fn validate(&self) -> anyhow::Result<()> {
        if self.slice_seconds.is_some() == self.slice_applications.is_some() {
            bail!("set exactly one of slice seconds and slice applications");
        }
        if self.budget_seconds.is_none()
            && self.budget_applications.is_none()
            && self.budget_slices.is_none()
        {
            bail!("set at least one budget");
        }
        if !(2..=self.size).contains(&self.tournament) {
            bail!("tournament must be between 2 and the population size");
        }
        Ok(())
    }
}

/// Per-slice log (protocol section 9).
#[derive(Debug, Clone, Default)]
pub struct SliceLog {
    start_ns: Vec<f64>,
    duration_ns: Vec<f64>,
    overshoot_ns: Vec<f64>,
    operator: Vec<Op>,
    previous: Vec<i64>,
    applications: Vec<u64>,
    improving: Vec<u64>,
    gain: Vec<i64>,
    length_before: Vec<i64>,
    length_after: Vec<i64>,
    accepted: Vec<bool>,
    lookups: Vec<u64>,
    moved: Vec<u64>,
    anchor: Vec<i64>,
    best_length: Vec<i64>,
    class: Vec<i64>,
}

/// Outcome of a run.
#[derive(Debug, Clone)]
pub struct RunResult {
    /// Best length found.
    pub best_length: i64,
    /// Hash of the best tour.
    pub best_hash: u64,
    /// Wall time charged to the budget, seconds.
    pub elapsed_seconds: f64,
    /// Time spent classifying (not charged), seconds.
    pub uncharged_seconds: f64,
    /// Per-slice log.
    pub log: SliceLog,
}

/// Time between two deadline checks within a time-bounded slice (ns).
const DEADLINE_CHECK_NS: f64 = 1000.0;

struct PopulationJob<'a> {
    arm: Arm,
    needs_class: bool,
    config: &'a Config,
    seed: u64,
    clock: &'a Clock,
}

/// Runs one slice: time mode when `deadline_ns` is given (TSC ticks), else
/// `count` applications.
fn run_slice<D: Dist, const C: bool, L: Layout>(
    op: Op,
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    clock: &Clock,
    seconds: Option<f64>,
    count: Option<u64>,
) -> SliceRecord {
    let mut record = SliceRecord {
        anchor: -1,
        ..SliceRecord::default()
    };
    let before = tour.length();
    let started = tsc::start();
    let mut finished;
    match (count, seconds) {
        (Some(count), _) => {
            for _ in 0..count {
                let application = op.apply(tour, problem, rng, &mut record.work, None);
                tour.clear_log();
                if application.delta < 0 {
                    record.improving += 1;
                    record.anchor = i64::from(application.anchor);
                }
            }
            record.applications = count;
            finished = tsc::stop();
        }
        (None, Some(seconds)) => {
            let deadline = started + (seconds * 1e9 * clock.ticks_per_ns) as u64;
            let interval = DEADLINE_CHECK_NS * clock.ticks_per_ns;
            // Reading the clock costs about as much as a cheap application,
            // and that cost would be charged to the operator; so the
            // deadline is checked every `ahead` applications, set from the
            // slice's pace so far to about `interval`, and never further
            // than the time left.
            let mut next_check = 1u64;
            loop {
                let application = op.apply(tour, problem, rng, &mut record.work, None);
                tour.clear_log();
                record.applications += 1;
                if application.delta < 0 {
                    record.improving += 1;
                    record.anchor = i64::from(application.anchor);
                }
                if record.applications < next_check {
                    continue;
                }
                finished = tsc::stop();
                if finished >= deadline {
                    break;
                }
                let pace = (finished - started) as f64 / record.applications as f64;
                let window = interval.min((deadline - finished) as f64);
                let ahead = (window / pace.max(1.0)) as u64;
                next_check = record.applications + ahead.max(1);
            }
            record.overshoot_ns = clock.ns(finished - deadline);
        }
        (None, None) => unreachable!("validated config has a slice limit"),
    }
    record.duration_ns = clock.ns(finished - started);
    record.gain = before - tour.length();
    record
}

impl Job for PopulationJob<'_> {
    type Output = RunResult;

    fn run<D: Dist, const C: bool, L: Layout>(
        self,
        instance: &Instance,
        problem: &Problem<'_, D, C>,
    ) -> anyhow::Result<RunResult> {
        let config = self.config;
        let clock = self.clock;
        let n = instance.n();
        let mut init = Stream::new(self.seed, "population:init", &[]);
        let mut population: Vec<Tour<L>> = (0..config.size)
            .map(|_| {
                let mut order: Vec<City> = (0..n as City).collect();
                init.shuffle(&mut order);
                tour_of::<L>(instance, &order)
            })
            .collect();
        let mut lengths: Vec<i64> = population.iter().map(Tour::length).collect();
        let mut select = Stream::new(self.seed, "population:select", &[]);
        let mut arm_rng = Stream::new(self.seed, "population:arm", &[]);
        let mut operator_rng = Stream::new(self.seed, "population:operator", &[]);
        let mut arm = self.arm;
        let mut log = SliceLog::default();
        let mut best = (0..config.size)
            .min_by_key(|&index| lengths[index])
            .expect("population");
        let mut previous = -1i64;
        let mut applications_total = 0u64;
        let mut uncharged = 0.0f64;
        let started = tsc::start();
        let elapsed_ns = |uncharged: f64| clock.ns(tsc::stop() - started) - uncharged;
        let mut slice = 0u64;
        loop {
            if config
                .budget_seconds
                .is_some_and(|budget| elapsed_ns(uncharged) >= budget * 1e9)
                || config
                    .budget_applications
                    .is_some_and(|budget| applications_total >= budget)
                || config.budget_slices.is_some_and(|budget| slice >= budget)
            {
                break;
            }
            let contenders = select.sample(config.size, config.tournament);
            let parent = *contenders
                .iter()
                .min_by_key(|&&index| (lengths[index], index))
                .expect("tournament");
            let mut child = population[parent].clone();
            let class = if self.needs_class {
                let begin = tsc::start();
                let class = classify(&child, problem);
                uncharged += clock.ns(tsc::stop() - begin);
                Some(class)
            } else {
                None
            };
            let (op, count) = arm.next(&mut arm_rng, class);
            let start_ns = elapsed_ns(uncharged);
            let record = run_slice(
                op,
                &mut child,
                problem,
                &mut operator_rng,
                clock,
                if count.is_some() {
                    None
                } else {
                    config.slice_seconds
                },
                count.or(config.slice_applications),
            );
            arm.update(op, &record);
            let worst = (0..config.size)
                .max_by_key(|&index| (lengths[index], std::cmp::Reverse(index)))
                .expect("population");
            let length = child.length();
            let accepted = length < lengths[worst] && !lengths.contains(&length);
            if accepted {
                population[worst] = child;
                lengths[worst] = length;
                if length < lengths[best] {
                    best = worst;
                }
            }
            applications_total += record.applications;
            log.start_ns.push(start_ns);
            log.duration_ns.push(record.duration_ns);
            log.overshoot_ns.push(record.overshoot_ns);
            log.operator.push(op);
            log.previous.push(previous);
            log.applications.push(record.applications);
            log.improving.push(record.improving);
            log.gain.push(record.gain);
            log.length_before.push(length + record.gain);
            log.length_after.push(length);
            log.accepted.push(accepted);
            log.lookups.push(record.work.lookups());
            log.moved.push(record.work.moved());
            log.anchor.push(record.anchor);
            log.best_length.push(lengths[best]);
            log.class.push(class.map_or(-1, |class| class as i64));
            previous = op as i64;
            slice += 1;
        }
        population[best]
            .check(&instance.matrix)
            .map_err(|message| anyhow::anyhow!("best tour: {message}"))?;
        Ok(RunResult {
            best_length: lengths[best],
            best_hash: population[best].hash(),
            elapsed_seconds: elapsed_ns(uncharged) / 1e9,
            uncharged_seconds: uncharged / 1e9,
            log,
        })
    }
}

/// Runs one population search.
///
/// # Errors
///
/// If the configuration is invalid or a level arm has no rate table.
#[allow(clippy::too_many_arguments)]
pub fn run_population(
    instance: &Instance,
    implementation: &Implementation,
    spec: &ArmSpec,
    table: Option<&Vec<Vec<f64>>>,
    config: &Config,
    seed: u64,
    clock: &Clock,
) -> anyhow::Result<RunResult> {
    config.validate()?;
    dispatch(
        PopulationJob {
            arm: Arm::new(spec, table)?,
            needs_class: spec.needs_class(),
            config,
            seed,
            clock,
        },
        instance,
        implementation,
    )
}

/// Re-runs a logged run exactly in count mode with the logged operators
/// and application counts.
///
/// # Errors
///
/// If the run fails.
pub fn replay(
    instance: &Instance,
    implementation: &Implementation,
    log: &SliceLog,
    config: &Config,
    seed: u64,
    clock: &Clock,
) -> anyhow::Result<RunResult> {
    let replay_config = Config {
        size: config.size,
        tournament: config.tournament,
        slice_seconds: None,
        slice_applications: Some(1),
        budget_seconds: None,
        budget_applications: None,
        budget_slices: Some(log.operator.len() as u64),
    };
    let entries: Vec<(Op, u64)> = log
        .operator
        .iter()
        .copied()
        .zip(log.applications.iter().copied())
        .collect();
    dispatch(
        PopulationJob {
            arm: Arm::Replay(entries, 0),
            needs_class: false,
            config: &replay_config,
            seed,
            clock,
        },
        instance,
        implementation,
    )
}

/// Per class and operator (in `Op::ALL` order), the median measured rate
/// over an instance's states of that class; classes without states on the
/// instance take the median over every instance.
///
/// # Errors
///
/// If the cells table cannot be read.
pub fn rate_table(cells: &Path, instance: &str) -> anyhow::Result<Vec<Vec<f64>>> {
    let batch = read(cells)?;
    let (names, classes, operators, rates) = (
        column_str(&batch, "instance")?,
        column_str(&batch, "class")?,
        column_str(&batch, "operator")?,
        column_f64(&batch, "rate")?,
    );
    let mut own: HashMap<(usize, usize), Vec<f64>> = HashMap::new();
    let mut all: HashMap<(usize, usize), Vec<f64>> = HashMap::new();
    for row in 0..names.len() {
        let class = CLASSES
            .iter()
            .position(|label| *label == classes[row])
            .context("unknown class")?;
        // Measurements made before 2026-11-05 still hold the retired operators.
        let Some(op) = Op::parse_stored(&operators[row])? else {
            continue;
        };
        let op = op.index();
        all.entry((class, op)).or_default().push(rates[row]);
        if names[row] == instance {
            own.entry((class, op)).or_default().push(rates[row]);
        }
    }
    Ok((0..CLASSES.len())
        .map(|class| {
            let source = if own.keys().any(|(c, _)| *c == class) {
                &own
            } else {
                &all
            };
            (0..Op::ALL.len())
                .map(|op| {
                    source
                        .get(&(class, op))
                        .map_or(0.0, |values| super::median(values))
                })
                .collect()
        })
        .collect())
}

/// Settings of a population experiment.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    /// Instance paths.
    pub instances: Vec<String>,
    /// Arms.
    pub arms: Vec<ArmSpec>,
    /// Run seeds (paired across arms).
    pub seeds: Vec<u64>,
    /// Per-run settings.
    pub config: Config,
    /// Measurement-A cells table for the level arms.
    pub rates: Option<String>,
    /// Storage, cache and representation.
    pub implementation: Implementation,
    /// Replay every run in count mode and require the identical best tour.
    pub check_replay: bool,
}

/// Runs every (instance, arm, seed) on `workers` pinned timed workers.
///
/// # Errors
///
/// If the machine is not locked (without `allow_unlocked`), a run fails or
/// a replay differs.
pub fn run(
    repository: &Path,
    directory: &Path,
    settings: &Settings,
    workers: usize,
    allow_unlocked: bool,
) -> anyhow::Result<()> {
    settings.config.validate()?;
    std::fs::create_dir_all(directory)?;
    let cores = pin::physical_cores()?;
    if workers + 1 > cores.len() {
        bail!(
            "{workers} timed workers need {} physical cores",
            workers + 1
        );
    }
    let problems = machine::require_locked(&cores, allow_unlocked)?;
    pin::pin_current_thread(*cores.last().expect("cores"))?;
    let clock = Clock::calibrate(5, 200)?;
    write_config(
        directory,
        repository,
        json!({
            "experiment": "population runs",
            "population": settings,
            "machine_problems": problems,
            "clock": clock,
            "timed_workers": workers,
        }),
    )?;
    let mut jobs = Vec::new();
    for path in &settings.instances {
        for arm in &settings.arms {
            for &seed in &settings.seeds {
                jobs.push((path.clone(), arm.clone(), seed));
            }
        }
    }
    let next = AtomicUsize::new(0);
    let errors: Mutex<Vec<anyhow::Error>> = Mutex::new(Vec::new());
    let summaries: Mutex<Vec<(String, String, u64, RunResult)>> = Mutex::new(Vec::new());
    let tables: Mutex<HashMap<String, Vec<Vec<f64>>>> = Mutex::new(HashMap::new());
    std::thread::scope(|scope| {
        for &cpu in &cores[..workers] {
            let (jobs, next, errors, summaries, tables, clock) =
                (&jobs, &next, &errors, &summaries, &tables, &clock);
            scope.spawn(move || {
                let result = (|| -> anyhow::Result<()> {
                    pin::pin_current_thread(cpu)?;
                    let mut cached: Option<(String, Instance)> = None;
                    while errors.lock().expect("lock").is_empty() {
                        let index = next.fetch_add(1, Ordering::Relaxed);
                        let Some((path, arm, seed)) = jobs.get(index) else {
                            return Ok(());
                        };
                        if cached
                            .as_ref()
                            .is_none_or(|(cached_path, _)| cached_path != path)
                        {
                            cached = Some((path.clone(), load_instance(repository, path)?));
                        }
                        let instance = &cached.as_ref().expect("loaded").1;
                        let key = format!("{}_{}_{seed}", instance.name, arm.label());
                        if has_part(directory, "runs", &key) {
                            continue;
                        }
                        let table = match (&settings.rates, arm.needs_class()) {
                            (Some(cells), true) => {
                                let mut tables = tables.lock().expect("lock");
                                if !tables.contains_key(&instance.name) {
                                    let cells = repository.join(cells);
                                    tables.insert(
                                        instance.name.clone(),
                                        rate_table(&cells, &instance.name)?,
                                    );
                                }
                                Some(tables[&instance.name].clone())
                            }
                            _ => None,
                        };
                        let result = run_population(
                            instance,
                            &settings.implementation,
                            arm,
                            table.as_ref(),
                            &settings.config,
                            *seed,
                            clock,
                        )
                        .with_context(|| key.clone())?;
                        if settings.check_replay {
                            let again = replay(
                                instance,
                                &settings.implementation,
                                &result.log,
                                &settings.config,
                                *seed,
                                clock,
                            )?;
                            if again.best_length != result.best_length
                                || again.best_hash != result.best_hash
                            {
                                bail!("{key}: count-mode replay reached another best tour");
                            }
                        }
                        write_slices(directory, &key, &instance.name, arm, *seed, &result)?;
                        log::info!(
                            "{key}: best {} after {} slices, {:.1} s",
                            result.best_length,
                            result.log.operator.len(),
                            result.elapsed_seconds
                        );
                        summaries.lock().expect("lock").push((
                            instance.name.clone(),
                            arm.label(),
                            *seed,
                            result,
                        ));
                    }
                    Ok(())
                })();
                if let Err(error) = result {
                    errors.lock().expect("lock").push(error);
                }
            });
        }
    });
    if let Some(error) = errors.into_inner().expect("finished").into_iter().next() {
        return Err(error);
    }
    if !std::fs::read_dir(directory.join("runs.parts"))
        .map(|mut entries| entries.next().is_none())
        .unwrap_or(true)
    {
        merge_parts(directory, "runs")?;
    }
    let summaries = summaries.into_inner().expect("finished");
    let mut text = String::from(
        "| Instance | Arm | Seed | Best length | Slices | Seconds |\n|---|---|---|---|---|---|\n",
    );
    for (instance, arm, seed, result) in &summaries {
        text += &format!(
            "| {instance} | {arm} | {seed} | {} | {} | {:.2} |\n",
            result.best_length,
            result.log.operator.len(),
            result.elapsed_seconds
        );
    }
    write_atomic(&directory.join("summary.md"), text.as_bytes())?;
    Ok(())
}

fn write_slices(
    directory: &Path,
    key: &str,
    instance: &str,
    arm: &ArmSpec,
    seed: u64,
    result: &RunResult,
) -> anyhow::Result<()> {
    let log = &result.log;
    let count = log.operator.len();
    write_part(
        directory,
        "slices",
        key,
        Table::new()
            .with("start_ns", Column::F64(log.start_ns.clone()))
            .with("duration_ns", Column::F64(log.duration_ns.clone()))
            .with("overshoot_ns", Column::F64(log.overshoot_ns.clone()))
            .with(
                "operator",
                Column::Str(
                    log.operator
                        .iter()
                        .map(|op| op.name().to_string())
                        .collect(),
                ),
            )
            .with("previous", Column::I64(log.previous.clone()))
            .with(
                "applications",
                Column::I64(log.applications.iter().map(|&v| v as i64).collect()),
            )
            .with(
                "improving",
                Column::I64(log.improving.iter().map(|&v| v as i64).collect()),
            )
            .with("gain", Column::I64(log.gain.clone()))
            .with("length_before", Column::I64(log.length_before.clone()))
            .with("length_after", Column::I64(log.length_after.clone()))
            .with("accepted", Column::Bool(log.accepted.clone()))
            .with(
                "lookups",
                Column::I64(log.lookups.iter().map(|&v| v as i64).collect()),
            )
            .with(
                "moved",
                Column::I64(log.moved.iter().map(|&v| v as i64).collect()),
            )
            .with("anchor", Column::I64(log.anchor.clone()))
            .with("best_length", Column::I64(log.best_length.clone()))
            .with("class", Column::I64(log.class.clone())),
    )?;
    write_part(
        directory,
        "runs",
        key,
        Table::new()
            .with("instance", Column::Str(vec![instance.to_string()]))
            .with("arm", Column::Str(vec![arm.label()]))
            .with("seed", Column::I64(vec![seed as i64]))
            .with("best_length", Column::I64(vec![result.best_length]))
            .with("slices", Column::I64(vec![count as i64]))
            .with("elapsed_seconds", Column::F64(vec![result.elapsed_seconds]))
            .with(
                "uncharged_seconds",
                Column::F64(vec![result.uncharged_seconds]),
            ),
    )
}
