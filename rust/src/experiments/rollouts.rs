//! E1R: does the greedy E1 choice pay off later?  Rollouts for Claim 1.4
//! (a port of the Python E1R pipeline; `ideas/rust-port.md` §8.5).
//!
//! For a sample of measured states s, every candidate first operator o
//! (the leader G(s), the policy's choice, the next operators by rate) gets
//! `repeats` rollouts: one slice of o, then the continuation policy (the
//! measured leader of the tour's exact class, "greedy by level"), on a
//! virtual clock, up to a horizon.  Rollouts of all candidates of a state
//! share the continuation's random stream per repeat, so they are paired.
//!
//! **Virtual clock.**  Each application is charged `a + b·lookups +
//! c·moved` ns, fitted per instance and operator (non-negative least
//! squares): first on measurement A's single-core timings (`plan`; the
//! per-call times scaled to each state's batched mean), then refitted on
//! timed rollouts on one pinned core (`calibrate`), where the clock is
//! read around whole slices, so the applications run back to back exactly
//! as in an untimed rollout and the fit is a regression of slice time on
//! the slice's application count, lookups and positions moved.  A rollout
//! is then fully determined by its seeds: `run` executes them untimed on
//! every CPU and `validate` replays some with real timing (also per
//! slice), which must reproduce them exactly.
//!
//! **Stall rule.**  With `none`, the policy always
//! applies its class leader, as in the Python test run, where it stalled:
//! a tour that only the full 2-opt scan can improve is "2-opt open", but
//! the class leader (O14) cannot improve it.  With `skip`, a policy slice
//! that gains nothing moves the choice to the next operator ranked by how
//! many measured states of that class it leads, until the tour improves.
//!
//! Stages: `plan`, `calibrate`, `run`, `validate`, `analyse`; outputs in
//! the run directory (`plan.json`, `cost_models*.json`, `*.parts/`,
//! `arms.parquet`, `states.parquet`, `summary.parquet`, `summary.md`).

use std::collections::{BTreeMap, HashMap};
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};

use anyhow::{Context, bail};
use serde::{Deserialize, Serialize};
use serde_json::json;

use super::common::{
    CLASSES, Implementation, InstanceCache, Job, dispatch, load_instance, parallel_map,
    read_states_directory, untimed_workers,
};
use super::marginal::{self, LeaderRecord, classify};
use super::states::{STATE_NAMES, state_rank};
use super::{mean, median, quantile, standard_deviation};
use crate::City;
use crate::instance::Instance;
use crate::instance::storage::Dist;
use crate::io::parquet::{
    Column, Table, column_f64, column_i64, has_part, read_part, write, write_part,
};
use crate::io::provenance::{read_settings, write_config};
use crate::io::rundir::{read_json, write_atomic};
use crate::ops::{Op, Problem, Work};
use crate::rng::{Stream, label_hash};
use crate::timing::session::{CoreRoles, TimedSession};
use crate::timing::tsc::{self, Clock};
use crate::tour::{Layout, Tour};

/// Size groups of the E1 instance set; an instance belongs to the nearest.
const SIZES: [usize; 3] = [100, 200, 500];
/// Bootstrap resamples per paired interval.
const BOOTSTRAP: usize = 2000;
/// Random sign flips per permutation test.
const FLIPS: usize = 10_000;
/// Fewest paired repeats a test is made on.
const MIN_REPEATS: usize = 8;
/// Family-wise error per state and horizon (Bonferroni over alternatives).
const ALPHA: f64 = 0.05;

/// State seeds whose timings fit the provisional clock at `plan`.
pub const FIT_SEEDS: [u64; 3] = [0, 1, 2];

/// State seed on which the provisional fit is checked.
pub const CHECK_SEED: u64 = 3;

/// Seed of the plan's draws and the rollouts' streams.
pub const PLAN_SEED: u64 = 0;

/// Rollouts `validate` replays with real timing unless told otherwise.
pub const VALIDATE_ROLLOUTS: usize = 24;

/// The continuation policy's behaviour when its operator gains nothing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, clap::ValueEnum)]
#[serde(rename_all = "snake_case")]
pub enum StallRule {
    /// Always the class leader (the Python test run).
    None,
    /// After a slice without gain, the next operator by lead count.
    Skip,
}

/// Settings of one E1R run (fixed at `plan`).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    /// The measurement-A run directory.
    pub measurement: String,
    /// Instances drawn per (family, size) cell.
    pub instances_per_cell: usize,
    /// States drawn per instance and class.
    pub states_per_class: usize,
    /// First operators per state.
    pub candidates: usize,
    /// Rollouts per (state, candidate), paired by repeat.
    pub repeats: usize,
    /// Virtual slice length per city, ns.
    pub slice_ns_per_city: f64,
    /// Horizon in slices, first slice included.
    pub horizon_slices: usize,
    /// State seeds whose timings fit the provisional clock.
    pub fit_seeds: Vec<u64>,
    /// State seed on which the provisional fit is checked.
    pub check_seed: u64,
    /// Seed of the plan's draws and the rollouts' streams.
    pub seed: u64,
    /// Rollouts replayed with real timing in `validate`.
    pub validate_rollouts: usize,
    /// Horizon of the calibration rollouts, in slices.
    pub calibration_slices: usize,
    /// Continuation policy on a slice without gain.
    pub stall_rule: StallRule,
}

// ---------------------------------------------------------- cost model

/// Predicted time of one application, `intercept + per_lookup · lookups +
/// per_moved · moved` ns (all non-negative).
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
pub struct Coefficients {
    /// Fixed cost per application.
    pub intercept: f64,
    /// Cost per distance read.
    pub per_lookup: f64,
    /// Cost per tour position rewritten.
    pub per_moved: f64,
}

/// Sufficient statistics of a least-squares fit of time on work.  A
/// sample is a group of `n` applications (one application, or a whole
/// slice) with summed lookups `l`, summed positions moved `m` and measured
/// time `t`; the model is `t = a·n + b·l + c·m`, so `a` is the fixed cost
/// per application.  For single applications (`n` = 1) this is the usual
/// fit with an intercept.
#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub struct Sums {
    /// Σ x xᵀ for x = (n, l, m): nn, nl, nm, ll, lm, mm.
    gram: [f64; 6],
    /// Σ t·x: tn, tl, tm.
    moment: [f64; 3],
    /// Applications over all samples.
    pub applications: f64,
    /// Lookups over all samples.
    pub lookups: f64,
    /// Positions moved over all samples.
    pub moved: f64,
    /// Measured time over all samples (ns).
    pub time_ns: f64,
}

impl Sums {
    /// Adds one application.
    pub fn add(&mut self, lookups: f64, moved: f64, time_ns: f64) {
        self.add_group(1.0, lookups, moved, time_ns);
    }

    /// Adds one sample of `applications` applications with their summed
    /// work and their measured time together.
    pub fn add_group(&mut self, applications: f64, lookups: f64, moved: f64, time_ns: f64) {
        let (n, l, m, t) = (applications, lookups, moved, time_ns);
        for (value, add) in self
            .gram
            .iter_mut()
            .zip([n * n, n * l, n * m, l * l, l * m, m * m])
        {
            *value += add;
        }
        for (value, add) in self.moment.iter_mut().zip([t * n, t * l, t * m]) {
            *value += add;
        }
        self.applications += n;
        self.lookups += l;
        self.moved += m;
        self.time_ns += t;
    }

    /// Adds another set of sums.
    pub fn merge(&mut self, other: &Sums) {
        for (value, add) in self.gram.iter_mut().zip(other.gram) {
            *value += add;
        }
        for (value, add) in self.moment.iter_mut().zip(other.moment) {
            *value += add;
        }
        self.applications += other.applications;
        self.lookups += other.lookups;
        self.moved += other.moved;
        self.time_ns += other.time_ns;
    }

    /// Time the coefficients predict for all samples together (ns).
    pub fn predicted_ns(&self, c: &Coefficients) -> f64 {
        c.intercept * self.applications + c.per_lookup * self.lookups + c.per_moved * self.moved
    }

    /// The least-squares fit with every coefficient non-negative and a
    /// positive intercept (exact non-negative least squares over the models
    /// that keep the intercept).  The optimum is the unconstrained
    /// least-squares fit on its own support, so with three features it is
    /// the best of the non-negative least-squares fits on the four feature
    /// subsets that contain the intercept, each solved with `nalgebra`'s SVD
    /// (minimum-norm where features are collinear: a double bridge reads
    /// the same number of distances every time, so its lookups are a
    /// multiple of its applications).  Until 2026-11-02 a hand-written
    /// elimination dropped the most negative coefficient and refitted,
    /// which need not reach the optimum.
    ///
    /// The intercept is kept because every application has a fixed cost
    /// (the call, its random draws, its anchor).  On 2026-11-03 the fit over
    /// all seven subsets put the whole cost on the moved cities in 18 of
    /// 822 (instance, operator) fits of an E1R run (for example
    /// `two_opt_nl` on d198: 26,900 ns per moved city, nothing otherwise):
    /// moves are rare and costly, so the fit charged an application that
    /// moved nothing 0 ns and the run stopped.  The intercept alone is
    /// always feasible (the mean time per application, positive), so a fit
    /// always exists.  The Python reference refused a fit with neither an
    /// intercept nor a per-lookup cost.
    ///
    /// # Errors
    ///
    /// If there are no applications or the fit predicts no positive time.
    pub fn fit(&self) -> anyhow::Result<Coefficients> {
        use nalgebra::{Matrix3, Vector3};
        let [nn, nl, nm, ll, lm, mm] = self.gram;
        if self.applications <= 0.0 {
            bail!("no applications to fit");
        }
        let gram = Matrix3::new(nn, nl, nm, nl, ll, lm, nm, lm, mm);
        let moment = Vector3::from(self.moment);
        // Squared residual up to the constant sum of squared times.
        let objective =
            |values: &Vector3<f64>| values.dot(&(gram * values)) - 2.0 * values.dot(&moment);
        let mut best: Option<(f64, Vector3<f64>)> = None;
        // Subsets with bit 0 (the intercept) set: 1, 3, 5, 7.
        for subset in (1u8..8).step_by(2) {
            let active: Vec<usize> = (0..3).filter(|index| subset & (1 << index) != 0).collect();
            let Some(values) = least_squares(&gram, &moment, &active) else {
                continue;
            };
            if values.iter().any(|value| *value < 0.0) {
                continue;
            }
            let value = objective(&values);
            if best.is_none_or(|(lowest, _)| value < lowest) {
                best = Some((value, values));
            }
        }
        let values = best.map_or_else(Vector3::zeros, |(_, values)| values);
        let coefficients = Coefficients {
            intercept: values[0],
            per_lookup: values[1],
            per_moved: values[2],
        };
        if coefficients.intercept <= 0.0 {
            bail!(
                "cost fit has no positive intercept ({} ns over {} applications)",
                self.time_ns,
                self.applications
            );
        }
        Ok(coefficients)
    }
}

/// Least-squares solution of the normal equations restricted to the
/// features `active` (SVD, minimum norm: a singular direction gets no
/// weight), the other coefficients zero; `None` if the SVD fails.
fn least_squares(
    gram: &nalgebra::Matrix3<f64>,
    moment: &nalgebra::Vector3<f64>,
    active: &[usize],
) -> Option<nalgebra::Vector3<f64>> {
    use nalgebra::{DMatrix, DVector, Vector3};
    let k = active.len();
    let restricted = DMatrix::from_fn(k, k, |row, column| gram[(active[row], active[column])]);
    let rhs = DVector::from_fn(k, |row, _| moment[active[row]]);
    let threshold = 1e-12 * restricted.amax().max(f64::MIN_POSITIVE);
    let solution = restricted.svd(true, true).solve(&rhs, threshold).ok()?;
    let mut values = Vector3::zeros();
    for (index, value) in active.iter().zip(solution.iter()) {
        values[*index] = *value;
    }
    Some(values)
}

/// Per-operator coefficients of one instance.
pub type CostModel = BTreeMap<String, Coefficients>;

impl Coefficients {
    /// Predicted time of one application with the counted `work` (ns).
    #[inline]
    pub fn cost_ns(&self, work: &Work) -> f64 {
        self.intercept
            + self.per_lookup * work.lookups() as f64
            + self.per_moved * work.moved() as f64
    }
}

/// The coefficients of `op` in `model`.
///
/// # Panics
///
/// If the model has none (every operator a run needs is checked at plan).
fn coefficients(model: &CostModel, op: Op) -> Coefficients {
    *model
        .get(op.name())
        .unwrap_or_else(|| panic!("no cost coefficients for {} (checked at plan)", op.name()))
}

// ---------------------------------------------------------------- policy

/// Continuation policy: per class, operators in order of preference (the
/// class leader first).
#[derive(Debug, Clone)]
pub struct Policy {
    ranking: Vec<Vec<Op>>,
    stall: StallRule,
    cached_length: Option<i64>,
    class: usize,
    skip: usize,
}

impl Policy {
    fn new(ranking: Vec<Vec<Op>>, stall: StallRule) -> Self {
        Self {
            ranking,
            stall,
            cached_length: None,
            class: 0,
            skip: 0,
        }
    }

    /// The operator for the next slice and the tour's class.  The class is
    /// recomputed only when the tour changed (operators only shorten it,
    /// so an unchanged length means an unchanged tour).
    fn choose<D: Dist, const C: bool, L: Layout>(
        &mut self,
        tour: &Tour<L>,
        problem: &Problem<'_, D, C>,
        last_gain: i64,
    ) -> (Op, usize) {
        if self.cached_length != Some(tour.length()) {
            self.class = classify(tour, problem);
            self.cached_length = Some(tour.length());
            self.skip = 0;
        } else if self.stall == StallRule::Skip && last_gain == 0 {
            self.skip += 1;
        }
        let ranking = &self.ranking[self.class];
        (ranking[self.skip % ranking.len()], self.class)
    }
}

// --------------------------------------------------------------- rollout

/// What one rollout did.
#[derive(Debug, Clone, Default)]
pub struct RolloutResult {
    /// Virtual time after each improving application.
    pub event_ns: Vec<f64>,
    /// Tour length after it.
    pub event_length: Vec<i64>,
    /// Virtual start of every slice.
    pub slice_start_ns: Vec<f64>,
    /// Operator of every slice.
    pub slice_operator: Vec<Op>,
    /// Class at the slice's start (-1 for the first slice).
    pub slice_class: Vec<i64>,
    /// Applications per slice.
    pub slice_applications: Vec<u64>,
    /// Virtual time at the end.
    pub end_ns: f64,
    /// Final length.
    pub final_length: i64,
    /// Stopped because the optimum was reached.
    pub reached_optimum: bool,
    /// Measured time of the applications (timed mode).
    pub real_ns: f64,
    /// Measured and virtual time per operator (timed mode).
    pub by_operator: BTreeMap<String, (f64, f64)>,
    /// Work and time sums per operator, one sample per slice (timed mode).
    pub sums: BTreeMap<String, Sums>,
}

/// One rollout's inputs.
struct Rollout<'a> {
    order: &'a [City],
    first: Op,
    ranking: &'a [Vec<Op>],
    stall: StallRule,
    model: &'a CostModel,
    slice_ns: f64,
    horizon_ns: f64,
    first_stream: Stream,
    continuation_stream: Stream,
    clock: Option<&'a Clock>,
}

impl Job for Rollout<'_> {
    type Output = RolloutResult;

    fn run<D: Dist, const C: bool, L: Layout>(
        self,
        instance: &Instance,
        problem: &Problem<'_, D, C>,
    ) -> anyhow::Result<RolloutResult> {
        if self.slice_ns <= 0.0 || self.horizon_ns < self.slice_ns {
            bail!("need 0 < slice <= horizon");
        }
        let mut tour = Tour::<L>::new(self.order, &instance.matrix);
        if self.clock.is_some() {
            crate::timing::disturb::spin_up();
        }
        let mut policy = Policy::new(self.ranking.to_vec(), self.stall);
        let mut result = RolloutResult::default();
        let mut clock_ns = 0.0;
        let mut op = self.first;
        let mut class = -1i64;
        let mut first = self.first_stream;
        let mut continuation = self.continuation_stream;
        let mut use_first = true;
        loop {
            result.slice_start_ns.push(clock_ns);
            result.slice_operator.push(op);
            result.slice_class.push(class);
            let rng = if use_first {
                &mut first
            } else {
                &mut continuation
            };
            let end_ns = clock_ns + self.slice_ns;
            let before = tour.length();
            let costs = coefficients(self.model, op);
            let mut applications = 0u64;
            let (mut lookups, mut moved) = (0u64, 0u64);
            let mut virtual_ns = 0.0;
            // Timed mode reads the clock around the whole slice: the
            // applications run back to back exactly as untimed, and the
            // cost model is fitted on slice totals (`Sums::add_group`).
            let slice_begin = self.clock.map(|_| tsc::start());
            loop {
                let mut work = Work::default();
                let delta = op.apply(&mut tour, problem, rng, &mut work, None).delta;
                tour.clear_log();
                let cost = costs.cost_ns(&work);
                if cost <= 0.0 {
                    bail!(
                        "{}: non-positive cost {cost} ns for {} ({} lookups, {} moved)",
                        instance.name,
                        op.name(),
                        work.lookups(),
                        work.moved()
                    );
                }
                lookups += work.lookups();
                moved += work.moved();
                clock_ns += cost;
                virtual_ns += cost;
                applications += 1;
                if delta < 0 {
                    result.event_ns.push(clock_ns);
                    result.event_length.push(tour.length());
                }
                if clock_ns >= end_ns {
                    break;
                }
            }
            if let (Some(clock), Some(begin)) = (self.clock, slice_begin) {
                let real = clock.region_ns(begin, tsc::stop());
                result.real_ns += real;
                result
                    .sums
                    .entry(op.name().to_string())
                    .or_default()
                    .add_group(applications as f64, lookups as f64, moved as f64, real);
                let entry = result.by_operator.entry(op.name().to_string()).or_default();
                entry.0 += real;
                entry.1 += virtual_ns;
            }
            result.slice_applications.push(applications);
            if instance
                .optimum
                .is_some_and(|optimum| tour.length() <= optimum)
            {
                result.reached_optimum = true;
                break;
            }
            if clock_ns >= self.horizon_ns {
                break;
            }
            let gain = before - tour.length();
            let (next, next_class) = policy.choose(&tour, problem, gain);
            op = next;
            class = next_class as i64;
            use_first = false;
        }
        result.end_ns = clock_ns;
        result.final_length = tour.length();
        tour.check(&instance.matrix)
            .map_err(|message| anyhow::anyhow!("rollout tour: {message}"))?;
        Ok(result)
    }
}

/// The tour length at virtual time `at_ns`: after the last improvement at
/// or before it, else `initial`.
fn length_at(initial: i64, event_ns: &[f64], event_length: &[i64], at_ns: f64) -> i64 {
    let index = event_ns.partition_point(|&time| time <= at_ns);
    if index == 0 {
        initial
    } else {
        event_length[index - 1]
    }
}

// ------------------------------------------------------------------ plan

/// One planned state.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlannedState {
    /// Instance name.
    pub instance: String,
    /// Instance path.
    pub path: String,
    /// Instance family.
    pub family: String,
    /// Number of cities.
    pub n: usize,
    /// State seed.
    pub seed: u64,
    /// State name.
    pub state: String,
    /// Exact class label.
    pub class: String,
    /// The measured leader G(s).
    pub greedy: String,
    /// First operators to roll out.
    pub candidates: Vec<String>,
    /// Their measured rates.
    pub rates: BTreeMap<String, f64>,
    /// The state tour.
    pub order: Vec<City>,
}

/// The plan of a run.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Plan {
    /// Chosen instances.
    pub instances: Vec<String>,
    /// Per instance and class, the policy's operators in order of
    /// preference (leader first).
    pub rankings: BTreeMap<String, Vec<Vec<String>>>,
    /// The states.
    pub states: Vec<PlannedState>,
    /// The implementation of the measurement run (storage, cache, layout).
    pub implementation: Implementation,
}

/// The first `count` (at most `population`) of a random order of
/// `0..population`.
///
/// The stream always draws a whole order, so the number of values it uses
/// depends only on `population`. Two plans with the same seed that ask for
/// different counts therefore stay in step, and the smaller draw is a prefix
/// of the larger: 3 states per class are the first 3 of 6, so runs with
/// different sample sizes share their states by construction.
fn nested_draw(rng: &mut Stream, population: usize, count: usize) -> Vec<usize> {
    let mut order: Vec<usize> = (0..population).collect();
    rng.shuffle(&mut order);
    order.truncate(count.min(population));
    order
}

fn size_group(n: usize) -> usize {
    *SIZES
        .iter()
        .min_by_key(|size| size.abs_diff(n))
        .expect("sizes exist")
}

/// Drops the retired operators from a measurement made before their
/// retirement (2026-11-05): their rates go, and a state they led gets the
/// best-rated current operator as its leader (ties to the lower protocol
/// number), improving only if that rate is positive.
///
/// # Errors
///
/// If a table names an operator that is neither current nor retired, or a
/// state led by a retired operator has no rates.
fn without_retired(
    mut leaders: Vec<LeaderRecord>,
    mut rates: marginal::RateMap,
) -> anyhow::Result<(Vec<LeaderRecord>, marginal::RateMap)> {
    for state_rates in rates.values_mut() {
        let mut kept = HashMap::with_capacity(state_rates.len());
        for (name, rate) in state_rates.drain() {
            if Op::parse_stored(&name)?.is_some() {
                kept.insert(name, rate);
            }
        }
        *state_rates = kept;
    }
    let mut relabelled = 0;
    for row in &mut leaders {
        if Op::parse_stored(&row.leader)?.is_some() {
            continue;
        }
        let state_rates = rates
            .get(&(row.instance.clone(), row.seed as u32, row.state.clone()))
            .with_context(|| format!("{} s{} {}: no rates", row.instance, row.seed, row.state))?;
        let (leader, rate) = Op::ALL
            .into_iter()
            .filter_map(|op| state_rates.get(op.name()).map(|&rate| (op, rate)))
            .max_by(|a, b| a.1.total_cmp(&b.1).then(b.0.cmp(&a.0)))
            .with_context(|| {
                format!(
                    "{} s{} {}: no current operator",
                    row.instance, row.seed, row.state
                )
            })?;
        log::info!(
            "plan: {} s{} {} was led by the retired {}; now {} (rate {rate:.3e})",
            row.instance,
            row.seed,
            row.state,
            row.leader,
            leader.name()
        );
        row.leader = leader.name().to_string();
        row.any_improvement = rate > 0.0;
        relabelled += 1;
    }
    if relabelled > 0 {
        log::info!("plan: {relabelled} states had a retired leader");
    }
    Ok((leaders, rates))
}

/// A filter of leader rows.
type Scope<'a> = Box<dyn Fn(&LeaderRecord) -> bool + 'a>;

/// Per class, the operators ranked by how many improving states of that
/// class they lead, in the narrowest scope that has any: the instance,
/// its (family, size) cell, its family, everything.  Ties go to the lower
/// operator number.
fn rankings(leaders: &[LeaderRecord], instance: &str) -> anyhow::Result<Vec<Vec<String>>> {
    let own = leaders
        .iter()
        .find(|row| row.instance == instance)
        .context("instance not in leaders")?;
    let (family, size) = (own.family.clone(), size_group(own.n));
    let scopes: [Scope<'_>; 4] = [
        Box::new(|row: &LeaderRecord| row.instance == instance),
        Box::new(|row: &LeaderRecord| row.family == family && size_group(row.n) == size),
        Box::new(|row: &LeaderRecord| row.family == own.family),
        Box::new(|_: &LeaderRecord| true),
    ];
    CLASSES
        .iter()
        .map(|label| {
            for scope in &scopes {
                let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
                for row in leaders
                    .iter()
                    .filter(|row| row.any_improvement && row.class == *label && scope(row))
                {
                    *counts.entry(row.leader.as_str()).or_default() += 1;
                }
                if counts.is_empty() {
                    continue;
                }
                let mut ranked: Vec<(&str, usize)> = counts.into_iter().collect();
                ranked.sort_by_key(|(name, count)| {
                    (
                        std::cmp::Reverse(*count),
                        Op::from_name(name).map_or(usize::MAX, |op| op as usize),
                    )
                });
                return Ok(ranked
                    .into_iter()
                    .map(|(name, _)| name.to_string())
                    .collect());
            }
            bail!("no measured leader for class {label}")
        })
        .collect()
}

/// Work and time of every timed application of an instance's states on
/// the given seeds, joined by (operator, anchor, repeat); per-call times
/// are scaled to the batched mean of their (state, operator).
fn timing_sums(
    measurement: &Path,
    instance: &str,
    seeds: &[u64],
    only: Option<&str>,
) -> anyhow::Result<BTreeMap<String, Sums>> {
    let mut sums: BTreeMap<String, Sums> = BTreeMap::new();
    for seed in seeds {
        for state in STATE_NAMES {
            if only.is_some_and(|name| name != state) {
                continue;
            }
            let key = marginal::state_key(instance, *seed, state);
            if !has_part(measurement, "timings", &key) {
                continue;
            }
            let work: HashMap<(String, City, u32), Work> = marginal::read_gains(measurement, &key)?
                .into_iter()
                .map(|record| ((record.operator, record.anchor, record.repeat), record.work))
                .collect();
            // Per-call times scaled to each operator's batched mean, the
            // time the measurement's rates use.
            let scales: HashMap<String, f64> = marginal::operator_times(measurement, &key)?
                .into_iter()
                .map(|(op, time)| (op, time.scale()))
                .collect();
            for record in marginal::read_timings(measurement, &key)? {
                let application = (record.operator, record.anchor, record.repeat);
                let work = work
                    .get(&application)
                    .with_context(|| format!("{key}: timing without pass-1 row"))?;
                let operator = application.0;
                let scale = *scales
                    .get(&operator)
                    .with_context(|| format!("{key}: no batched time of {operator}"))?;
                sums.entry(operator).or_default().add(
                    work.lookups() as f64,
                    work.moved() as f64,
                    record.time_ns * scale,
                );
            }
        }
    }
    Ok(sums)
}

/// Stage `plan`.
///
/// # Errors
///
/// If the measurement run lacks tables, a class has no leader, or the
/// provisional clock cannot be fitted.
pub fn plan(repository: &Path, directory: &Path, settings: &Settings) -> anyhow::Result<()> {
    if directory.join("plan.json").is_file() {
        bail!(
            "{} already has a plan; use a new directory",
            directory.display()
        );
    }
    std::fs::create_dir_all(directory)?;
    let measurement = repository.join(&settings.measurement);
    let measured = marginal::read_settings(&measurement).context("measurement run config")?;
    let (leaders, rates) = without_retired(
        marginal::read_leaders(&measurement.join("leaders.parquet"))?,
        marginal::read_rates(&measurement.join("cells.parquet"))?,
    )?;
    let mut rng = Stream::new(settings.seed, "e1r-plan", &[]);
    let mut cells: BTreeMap<(String, usize), Vec<String>> = BTreeMap::new();
    let mut seen = std::collections::BTreeSet::new();
    for row in &leaders {
        if seen.insert(row.instance.clone()) {
            cells
                .entry((row.family.clone(), size_group(row.n)))
                .or_default()
                .push(row.instance.clone());
        }
    }
    let mut chosen: Vec<String> = Vec::new();
    for members in cells.values_mut() {
        members.sort();
        let picks = nested_draw(&mut rng, members.len(), settings.instances_per_cell);
        chosen.extend(picks.into_iter().map(|index| members[index].clone()));
    }
    chosen.sort();
    let files = read_states_directory(&repository.join(&measured.states))?;
    let orders: HashMap<(String, u64, String), (String, Vec<City>)> = files
        .into_iter()
        .flat_map(|file| {
            let (instance, path, seed) = (file.instance, file.path, file.state_seed);
            file.states.into_iter().map(move |state| {
                (
                    (instance.clone(), seed, state.name),
                    (path.clone(), state.order),
                )
            })
        })
        .collect();
    let mut planned = Vec::new();
    let mut all_rankings = BTreeMap::new();
    for instance in &chosen {
        let ranking = rankings(&leaders, instance)?;
        for (class_index, label) in CLASSES.iter().enumerate() {
            let mut pool: Vec<&LeaderRecord> = leaders
                .iter()
                .filter(|row| {
                    &row.instance == instance && row.class == *label && row.any_improvement
                })
                .collect();
            pool.sort_by_key(|row| (row.seed, state_rank(&row.state)));
            for pick in nested_draw(&mut rng, pool.len(), settings.states_per_class) {
                let row = pool[pick];
                let state_rates = rates
                    .get(&(row.instance.clone(), row.seed as u32, row.state.clone()))
                    .context("state missing from cells")?;
                let greedy = row.leader.clone();
                let best = state_rates.values().copied().fold(f64::MIN, f64::max);
                if state_rates[&greedy] < best {
                    bail!(
                        "{instance} s{} {}: leaders and cells disagree",
                        row.seed,
                        row.state
                    );
                }
                let mut candidates = vec![greedy.clone()];
                let choice = &ranking[class_index][0];
                if *choice != greedy && state_rates[choice] > 0.0 {
                    candidates.push(choice.clone());
                }
                let mut by_rate: Vec<(&String, f64)> = state_rates
                    .iter()
                    .map(|(name, rate)| (name, *rate))
                    .collect();
                by_rate.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.cmp(b.0)));
                for (name, rate) in by_rate {
                    if candidates.len() >= settings.candidates {
                        break;
                    }
                    if !candidates.contains(name) && rate > 0.0 {
                        candidates.push(name.clone());
                    }
                }
                let (path, order) = orders
                    .get(&(row.instance.clone(), row.seed, row.state.clone()))
                    .context("state tour missing")?
                    .clone();
                planned.push(PlannedState {
                    instance: instance.clone(),
                    path,
                    family: row.family.clone(),
                    n: row.n,
                    seed: row.seed,
                    state: row.state.clone(),
                    class: label.to_string(),
                    greedy,
                    rates: candidates
                        .iter()
                        .map(|name| (name.clone(), state_rates[name]))
                        .collect(),
                    candidates,
                    order,
                });
            }
        }
        all_rankings.insert(instance.clone(), ranking);
    }
    // Provisional clock, checked on the held-out seed.
    let mut models: BTreeMap<String, CostModel> = BTreeMap::new();
    let (mut c_instance, mut c_op, mut c_state, mut c_error) = (vec![], vec![], vec![], vec![]);
    for instance in &chosen {
        let fit = timing_sums(&measurement, instance, &settings.fit_seeds, None)?;
        let mut model = CostModel::new();
        for op in Op::ALL {
            let sums = fit
                .get(op.name())
                .with_context(|| format!("{instance}: no timings of {}", op.name()))?;
            model.insert(
                op.name().to_string(),
                sums.fit()
                    .with_context(|| format!("{instance}: fitting {}", op.name()))?,
            );
        }
        // Held-out check: predicted against measured mean time, per state
        // of the check seed and operator.
        for state in STATE_NAMES {
            let check = timing_sums(&measurement, instance, &[settings.check_seed], Some(state))?;
            for (name, sums) in &check {
                let c = model[name];
                c_instance.push(instance.clone());
                c_op.push(name.clone());
                c_state.push(marginal::state_key(instance, settings.check_seed, state));
                c_error.push(sums.predicted_ns(&c) / sums.time_ns - 1.0);
            }
        }
        models.insert(instance.clone(), model);
    }
    write(
        &directory.join("cost_check.parquet"),
        Table::new()
            .with("instance", Column::Str(c_instance))
            .with("operator", Column::Str(c_op))
            .with("state", Column::Str(c_state))
            .with("relative_error", Column::F64(c_error)),
    )?;
    write_atomic(
        &directory.join("cost_models_e1.json"),
        serde_json::to_string_pretty(&models)?.as_bytes(),
    )?;
    let plan = Plan {
        instances: chosen,
        rankings: all_rankings,
        states: planned,
        implementation: measured.implementation,
    };
    write_atomic(
        &directory.join("plan.json"),
        serde_json::to_string(&plan)?.as_bytes(),
    )?;
    write_config(
        directory,
        repository,
        json!({ "experiment": "E1R rollouts (Claim 1.4)", "e1r": settings }),
    )?;
    let rollouts: usize = plan
        .states
        .iter()
        .map(|s| s.candidates.len())
        .sum::<usize>()
        * settings.repeats;
    let virtual_s: f64 = plan
        .states
        .iter()
        .map(|s| {
            s.candidates.len() as f64
                * settings.repeats as f64
                * settings.horizon_slices as f64
                * settings.slice_ns_per_city
                * s.n as f64
        })
        .sum::<f64>()
        / 1e9;
    log::info!(
        "plan: {} instances, {} states, {rollouts} rollouts, at most {:.2} virtual core-hours",
        plan.instances.len(),
        plan.states.len(),
        virtual_s / 3600.0
    );
    Ok(())
}

// ------------------------------------------------------- run directory

/// One (state, candidate) pair.
#[derive(Debug, Clone)]
struct Task {
    state: usize,
    candidate: Op,
}

/// Reads the settings stored at `plan`.
///
/// # Errors
///
/// If the run has no plan.
fn load_settings(directory: &Path) -> anyhow::Result<Settings> {
    read_settings(directory, "e1r").context("E1R settings")
}

fn tasks_of(plan: &Plan) -> anyhow::Result<Vec<Task>> {
    let mut tasks = Vec::new();
    for (index, state) in plan.states.iter().enumerate() {
        for name in &state.candidates {
            tasks.push(Task {
                state: index,
                candidate: Op::parse_current(name)?,
            });
        }
    }
    tasks.sort_by_key(|task| {
        (
            std::cmp::Reverse(plan.states[task.state].n),
            task.state,
            task.candidate,
        )
    });
    Ok(tasks)
}

fn label(state: &PlannedState) -> String {
    marginal::state_label(&state.instance, state.seed, &state.state)
}

fn task_key(state: &PlannedState, candidate: Op) -> String {
    format!(
        "{}_{}",
        marginal::state_key(&state.instance, state.seed, &state.state),
        candidate.name()
    )
}

fn ranking_ops(plan: &Plan, instance: &str) -> anyhow::Result<Vec<Vec<Op>>> {
    plan.rankings[instance]
        .iter()
        .map(|class| class.iter().map(|name| Op::parse_current(name)).collect())
        .collect()
}

/// The first-slice and continuation streams of one rollout.
struct Streams {
    first: Stream,
    continuation: Stream,
}

impl Streams {
    /// The streams of repeat `repeat` of a state's rollouts (`run`, and
    /// their timed replays in `validate`), shared by every candidate.
    fn repeat(settings: &Settings, state: &PlannedState, repeat: usize) -> Self {
        let keys = [label_hash(&label(state)), repeat as u64];
        Self {
            first: Stream::new(settings.seed, "e1r:first", &keys),
            continuation: Stream::new(settings.seed, "e1r:continue", &keys),
        }
    }

    /// The streams of a candidate's calibration rollout.
    fn calibration(settings: &Settings, state: &PlannedState, candidate: Op) -> Self {
        let keys = [label_hash(&label(state)), candidate as u64];
        Self {
            first: Stream::new(settings.seed, "e1r:calibrate-first", &keys),
            continuation: Stream::new(settings.seed, "e1r:calibrate", &keys),
        }
    }
}

/// One rollout of `task` over `horizon_slices` slices; timed when `clock`
/// is given.
#[allow(clippy::too_many_arguments)]
fn roll_out(
    instance: &Instance,
    plan: &Plan,
    settings: &Settings,
    model: &CostModel,
    task: &Task,
    horizon_slices: usize,
    streams: Streams,
    clock: Option<&Clock>,
) -> anyhow::Result<RolloutResult> {
    let state = &plan.states[task.state];
    let ranking = ranking_ops(plan, &state.instance)?;
    let slice_ns = settings.slice_ns_per_city * state.n as f64;
    dispatch(
        Rollout {
            order: &state.order,
            first: task.candidate,
            ranking: &ranking,
            stall: settings.stall_rule,
            model,
            slice_ns,
            horizon_ns: horizon_slices as f64 * slice_ns,
            first_stream: streams.first,
            continuation_stream: streams.continuation,
            clock,
        },
        instance,
        &plan.implementation,
    )
}

/// Stage `calibrate`: timed rollouts on one pinned core; refit the clock.
///
/// # Errors
///
/// If the machine is not locked (without `allow_unlocked`) or an operator
/// the run needs never gets timed.
pub fn calibrate(repository: &Path, directory: &Path, allow_unlocked: bool) -> anyhow::Result<()> {
    let settings = load_settings(directory)?;
    let plan: Plan = read_json(&directory.join("plan.json"))?;
    let provisional: BTreeMap<String, CostModel> =
        read_json(&directory.join("cost_models_e1.json"))?;
    let TimedSession {
        roles,
        problems,
        clock,
    } = TimedSession::open(CoreRoles::main_times()?, allow_unlocked)?;
    write_atomic(
        &directory.join("calibrate.json"),
        serde_json::to_string_pretty(
            &json!({"machine_problems": problems, "clock": clock, "cpu": roles.main}),
        )?
        .as_bytes(),
    )?;
    let tasks = tasks_of(&plan)?;
    let mut sums: BTreeMap<String, BTreeMap<String, Sums>> = BTreeMap::new();
    let mut instances = InstanceCache::new();
    let mut collect = |sums: &mut BTreeMap<String, BTreeMap<String, Sums>>,
                       task: &Task,
                       slices: usize|
     -> anyhow::Result<()> {
        let state = &plan.states[task.state];
        let instance = instances.get(repository, &state.path)?;
        let result = roll_out(
            &instance,
            &plan,
            &settings,
            &provisional[&state.instance],
            task,
            slices,
            Streams::calibration(&settings, state, task.candidate),
            Some(&clock),
        )?;
        let entry = sums.entry(state.instance.clone()).or_default();
        for (name, add) in &result.sums {
            entry.entry(name.clone()).or_default().merge(add);
        }
        Ok(())
    };
    let started = std::time::Instant::now();
    for (done, task) in tasks.iter().enumerate() {
        collect(&mut sums, task, settings.calibration_slices)?;
        if (done + 1).is_multiple_of(25) || done + 1 == tasks.len() {
            log::info!(
                "calibrate: {}/{} rollouts ({:.0} s)",
                done + 1,
                tasks.len(),
                started.elapsed().as_secs_f64()
            );
        }
    }
    let mut models: BTreeMap<String, CostModel> = BTreeMap::new();
    let (mut r_instance, mut r_op, mut r_count, mut r_measured, mut r_ratio) =
        (vec![], vec![], vec![], vec![], vec![]);
    for instance in &plan.instances {
        let indices: Vec<usize> = (0..plan.states.len())
            .filter(|&index| &plan.states[index].instance == instance)
            .collect();
        let mut needed: std::collections::BTreeSet<String> =
            plan.rankings[instance].iter().flatten().cloned().collect();
        for &index in &indices {
            needed.extend(plan.states[index].candidates.iter().cloned());
        }
        let have: Vec<String> = sums
            .get(instance)
            .map(|entry| entry.keys().cloned().collect())
            .unwrap_or_default();
        for name in needed.iter().filter(|name| !have.contains(name)) {
            log::info!("calibrate: {instance} never ran {name}; timing it directly");
            let op = Op::parse_current(name)?;
            for &index in &indices {
                collect(
                    &mut sums,
                    &Task {
                        state: index,
                        candidate: op,
                    },
                    1,
                )?;
            }
        }
        let entry = sums.get(instance).context("no timings at all")?;
        let mut model = CostModel::new();
        for name in &needed {
            let fitted = entry
                .get(name)
                .with_context(|| format!("{instance}: no timings for {name}"))?;
            model.insert(
                name.clone(),
                fitted
                    .fit()
                    .with_context(|| format!("{instance}: fitting {name}"))?,
            );
            let e1 = provisional[instance][name];
            r_instance.push(instance.clone());
            r_op.push(name.clone());
            r_count.push(fitted.applications as i64);
            r_measured.push(fitted.time_ns);
            r_ratio.push(fitted.predicted_ns(&e1) / fitted.time_ns);
        }
        models.insert(instance.clone(), model);
    }
    write_atomic(
        &directory.join("cost_models.json"),
        serde_json::to_string_pretty(&models)?.as_bytes(),
    )?;
    write(
        &directory.join("calibration.parquet"),
        Table::new()
            .with("instance", Column::Str(r_instance))
            .with("operator", Column::Str(r_op.clone()))
            .with("applications", Column::I64(r_count))
            .with("measured_ns", Column::F64(r_measured))
            .with("e1_fit_over_measured", Column::F64(r_ratio.clone())),
    )?;
    let mut by_op: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    for (name, ratio) in r_op.into_iter().zip(r_ratio) {
        by_op.entry(name).or_default().push(ratio);
    }
    for (name, ratios) in by_op {
        log::info!(
            "measurement-A fit / measured, {name}: median {:.3} over {} instances",
            median(&ratios),
            ratios.len()
        );
    }
    Ok(())
}

/// Stage `calibrate` without timing: takes the cost models of an already
/// calibrated run (`source`) instead of fitting new ones.
///
/// Runs that share one cost model run on the same virtual clock, so a
/// difference between them (a different slice length, say) is not mixed
/// with the fit noise of separate calibrations. The application cost model
/// does not depend on the slice length, and each run's `validate` stage
/// still replays rollouts with real timing, which checks that the shared
/// model holds for this run. The source directory and the SHA-256 of its
/// `cost_models.json` are recorded in `calibrate.json`.
///
/// The source file is copied byte for byte, not parsed and written again:
/// serde_json's default float parsing does not always round-trip (a
/// coefficient can move by one unit in the last place), and only identical
/// text guarantees that both runs read identical coefficients.
///
/// # Errors
///
/// If either run has no plan, the source is not calibrated, the two runs
/// measured different implementations, or the source lacks a model for an
/// instance or operator this run needs.
pub fn reuse_calibration(directory: &Path, source: &Path) -> anyhow::Result<()> {
    let plan: Plan = read_json(&directory.join("plan.json"))?;
    let source_plan: Plan = read_json(&source.join("plan.json"))?;
    if source_plan.implementation != plan.implementation {
        bail!(
            "{} measured {:?}, this run {:?}: their cost models are not interchangeable",
            source.display(),
            source_plan.implementation,
            plan.implementation
        );
    }
    let source_file = source.join("cost_models.json");
    let bytes = std::fs::read(&source_file)
        .with_context(|| format!("reading {}; calibrate it first", source_file.display()))?;
    let source_models: BTreeMap<String, CostModel> = serde_json::from_slice(&bytes)
        .with_context(|| format!("parsing {}", source_file.display()))?;
    let mut missing: Vec<String> = Vec::new();
    for instance in &plan.instances {
        let mut needed: std::collections::BTreeSet<&String> =
            plan.rankings[instance].iter().flatten().collect();
        needed.extend(
            plan.states
                .iter()
                .filter(|state| &state.instance == instance)
                .flat_map(|state| &state.candidates),
        );
        let Some(model) = source_models.get(instance) else {
            missing.push(format!("{instance} (every operator)"));
            continue;
        };
        missing.extend(
            needed
                .into_iter()
                .filter(|name| !model.contains_key(*name))
                .map(|name| format!("{instance}:{name}")),
        );
    }
    if !missing.is_empty() {
        bail!(
            "{} has no cost model for {}",
            source.display(),
            missing.join(", ")
        );
    }
    let source_settings = load_settings(source)?;
    write_atomic(&directory.join("cost_models.json"), &bytes)?;
    write_atomic(
        &directory.join("calibrate.json"),
        serde_json::to_string_pretty(&json!({
            "cost_models_from": source.display().to_string(),
            "cost_models_sha256": crate::instance::sha256_hex(&bytes),
            "source_slice_ns_per_city": source_settings.slice_ns_per_city,
        }))?
        .as_bytes(),
    )?;
    log::info!(
        "calibrate: cost models of {} instances taken from {} (calibrated at {} ns per city)",
        plan.instances.len(),
        source.display(),
        source_settings.slice_ns_per_city
    );
    Ok(())
}

/// Stage `run`: every task not yet written, untimed, on `workers` threads.
///
/// # Errors
///
/// If the clock is not calibrated yet or a rollout fails.
pub fn run(repository: &Path, directory: &Path, workers: usize) -> anyhow::Result<()> {
    let settings = load_settings(directory)?;
    let plan: Plan = read_json(&directory.join("plan.json"))?;
    let models: BTreeMap<String, CostModel> =
        read_json(&directory.join("cost_models.json")).context("run the calibrate stage first")?;
    let tasks = tasks_of(&plan)?;
    let todo: Vec<&Task> = tasks
        .iter()
        .filter(|task| {
            !has_part(
                directory,
                "repeats",
                &task_key(&plan.states[task.state], task.candidate),
            )
        })
        .collect();
    let workers = untimed_workers(workers);
    log::info!(
        "run: {} tasks to do, {} done, {workers} threads",
        todo.len(),
        tasks.len() - todo.len()
    );
    let done = AtomicUsize::new(0);
    let started = std::time::Instant::now();
    parallel_map(
        &todo,
        workers,
        |task| task_key(&plan.states[task.state], task.candidate),
        |task| {
            let state = &plan.states[task.state];
            let instance = load_instance(repository, &state.path)?;
            let results = (0..settings.repeats)
                .map(|repeat| {
                    let result = roll_out(
                        &instance,
                        &plan,
                        &settings,
                        &models[&state.instance],
                        task,
                        settings.horizon_slices,
                        Streams::repeat(&settings, state, repeat),
                        None,
                    )?;
                    Ok((repeat, result))
                })
                .collect::<anyhow::Result<Vec<_>>>()?;
            write_results(
                directory,
                &task_key(state, task.candidate),
                &instance,
                state,
                &settings,
                &results,
            )?;
            let count = done.fetch_add(1, Ordering::Relaxed) + 1;
            let elapsed = started.elapsed().as_secs_f64();
            log::info!(
                "{count}/{} {} ({:.0} min, about {:.0} min left)",
                todo.len(),
                task_key(state, task.candidate),
                elapsed / 60.0,
                elapsed / count as f64 * (todo.len() - count) as f64 / 60.0
            );
            Ok(())
        },
    )?;
    Ok(())
}

fn write_results(
    directory: &Path,
    key: &str,
    instance: &Instance,
    state: &PlannedState,
    settings: &Settings,
    results: &[(usize, RolloutResult)],
) -> anyhow::Result<()> {
    let initial = crate::instance::tour_length(&instance.matrix, &state.order);
    let slice_ns = settings.slice_ns_per_city * state.n as f64;
    let (mut e_repeat, mut e_ns, mut e_length) = (vec![], vec![], vec![]);
    let (mut s_repeat, mut s_start, mut s_op, mut s_class, mut s_apps) =
        (vec![], vec![], vec![], vec![], vec![]);
    let (mut r_repeat, mut r_end, mut r_final, mut r_optimum) = (vec![], vec![], vec![], vec![]);
    for (repeat, result) in results {
        e_repeat.extend(std::iter::repeat_n(*repeat as u32, result.event_ns.len()));
        e_ns.extend(&result.event_ns);
        e_length.extend(&result.event_length);
        s_repeat.extend(std::iter::repeat_n(
            *repeat as u32,
            result.slice_start_ns.len(),
        ));
        s_start.extend(&result.slice_start_ns);
        s_op.extend(result.slice_operator.iter().map(|op| op.name().to_string()));
        s_class.extend(&result.slice_class);
        s_apps.extend(result.slice_applications.iter().map(|&value| value as i64));
        r_repeat.push(*repeat as u32);
        r_end.push(result.end_ns);
        r_final.push(result.final_length);
        r_optimum.push(result.reached_optimum);
    }
    let count = r_repeat.len();
    write_part(
        directory,
        "events",
        key,
        Table::new()
            .with("repeat", Column::U32(e_repeat))
            .with("event_ns", Column::F64(e_ns))
            .with("event_length", Column::I64(e_length)),
    )?;
    write_part(
        directory,
        "slices",
        key,
        Table::new()
            .with("repeat", Column::U32(s_repeat))
            .with("start_ns", Column::F64(s_start))
            .with("operator", Column::Str(s_op))
            .with("class", Column::I64(s_class))
            .with("applications", Column::I64(s_apps)),
    )?;
    // Written last: marks the task done.
    write_part(
        directory,
        "repeats",
        key,
        Table::new()
            .with("repeat", Column::U32(r_repeat))
            .with("end_ns", Column::F64(r_end))
            .with("final_length", Column::I64(r_final))
            .with("reached_optimum", Column::Bool(r_optimum))
            .with("initial_length", Column::I64(vec![initial; count]))
            .with("slice_ns", Column::F64(vec![slice_ns; count])),
    )?;
    Ok(())
}

/// Stage `validate`: replays some rollouts with real timing on one pinned
/// core; each must reproduce its stored result exactly.
///
/// # Errors
///
/// If the run is not finished, the machine is not locked (without
/// `allow_unlocked`), or a replay differs.
pub fn validate(
    repository: &Path,
    directory: &Path,
    rollouts: Option<usize>,
    allow_unlocked: bool,
) -> anyhow::Result<()> {
    let settings = load_settings(directory)?;
    let plan: Plan = read_json(&directory.join("plan.json"))?;
    let models: BTreeMap<String, CostModel> = read_json(&directory.join("cost_models.json"))?;
    let tasks = tasks_of(&plan)?;
    if let Some(task) = tasks.iter().find(|task| {
        !has_part(
            directory,
            "repeats",
            &task_key(&plan.states[task.state], task.candidate),
        )
    }) {
        bail!(
            "{} not run yet",
            task_key(&plan.states[task.state], task.candidate)
        );
    }
    let TimedSession {
        roles: _,
        problems,
        clock,
    } = TimedSession::open(CoreRoles::main_times()?, allow_unlocked)?;
    let mut rng = Stream::new(settings.seed, "e1r-validate", &[]);
    let pairs: Vec<(usize, usize)> = (0..tasks.len())
        .flat_map(|task| (0..settings.repeats).map(move |repeat| (task, repeat)))
        .collect();
    let count = rollouts
        .unwrap_or(settings.validate_rollouts)
        .min(pairs.len());
    let (mut v_key, mut v_repeat, mut v_op, mut v_virtual, mut v_real) =
        (vec![], vec![], vec![], vec![], vec![]);
    for pick in rng.sample(pairs.len(), count) {
        let (task_index, repeat) = pairs[pick];
        let task = &tasks[task_index];
        let state = &plan.states[task.state];
        let key = task_key(state, task.candidate);
        let stored = read_part(directory, "repeats", &key)?;
        let row = column_i64(&stored, "repeat")?
            .iter()
            .position(|&value| value as usize == repeat)
            .context("stored repeat missing")?;
        let instance = load_instance(repository, &state.path)?;
        let result = roll_out(
            &instance,
            &plan,
            &settings,
            &models[&state.instance],
            task,
            settings.horizon_slices,
            Streams::repeat(&settings, state, repeat),
            Some(&clock),
        )?;
        if result.final_length != column_i64(&stored, "final_length")?[row]
            || result.end_ns != column_f64(&stored, "end_ns")?[row]
        {
            bail!("{key} repeat {repeat} not reproduced");
        }
        let mut totals = vec![("all".to_string(), (result.real_ns, result.end_ns))];
        totals.extend(result.by_operator.clone());
        for (name, (real, virtual_ns)) in totals {
            v_key.push(key.clone());
            v_repeat.push(repeat as u32);
            v_op.push(name);
            v_virtual.push(virtual_ns);
            v_real.push(real);
        }
        log::info!(
            "validate {key}:{repeat} real/virtual {:.3}",
            result.real_ns / result.end_ns
        );
    }
    let mut by_op: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    for ((name, real), virtual_ns) in v_op.iter().zip(&v_real).zip(&v_virtual) {
        by_op
            .entry(name.clone())
            .or_default()
            .push(real / virtual_ns);
    }
    for (name, ratios) in &by_op {
        log::info!(
            "real/virtual {name}: median {:.3}, min {:.3}, max {:.3} ({} rollouts)",
            median(ratios),
            ratios.iter().copied().fold(f64::MAX, f64::min),
            ratios.iter().copied().fold(f64::MIN, f64::max),
            ratios.len()
        );
    }
    write(
        &directory.join("validate.parquet"),
        Table::new()
            .with("task", Column::Str(v_key))
            .with("repeat", Column::U32(v_repeat))
            .with("operator", Column::Str(v_op))
            .with("virtual_ns", Column::F64(v_virtual))
            .with("real_ns", Column::F64(v_real)),
    )?;
    write_atomic(
        &directory.join("validate.json"),
        serde_json::to_string_pretty(&json!({"machine_problems": problems, "clock": clock}))?
            .as_bytes(),
    )?;
    Ok(())
}

// --------------------------------------------------------------- analyse

/// Horizons in slices: powers of two up to the run's horizon.
fn horizons(horizon_slices: usize) -> Vec<usize> {
    let mut result = vec![1];
    while result.last().expect("non-empty") * 2 <= horizon_slices {
        result.push(result.last().expect("non-empty") * 2);
    }
    result
}

/// Per repeat (sorted) and cut: the gap, and the virtual time at which the
/// rollout reached the length it has at the cut (its last improvement up
/// to the cut; 0 if it never improved).
type GapsAndTimes = (Vec<Vec<f64>>, Vec<Vec<f64>>);

/// Gaps of every repeat (sorted) at every cut, with the times they were
/// reached (see [`GapsAndTimes`]).
fn gaps(directory: &Path, key: &str, optimum: f64, cuts: &[f64]) -> anyhow::Result<GapsAndTimes> {
    let repeats = read_part(directory, "repeats", key)?;
    let events = read_part(directory, "events", key)?;
    let initial = column_i64(&repeats, "initial_length")?[0];
    let mut repeat_ids = column_i64(&repeats, "repeat")?;
    repeat_ids.sort_unstable();
    let (e_repeat, e_ns, e_length) = (
        column_i64(&events, "repeat")?,
        column_f64(&events, "event_ns")?,
        column_i64(&events, "event_length")?,
    );
    repeat_ids
        .iter()
        .map(|&repeat| {
            let (times, lengths): (Vec<f64>, Vec<i64>) = (0..e_repeat.len())
                .filter(|&row| e_repeat[row] == repeat)
                .map(|row| (e_ns[row], e_length[row]))
                .unzip();
            if times.windows(2).any(|pair| pair[1] < pair[0]) {
                bail!("{key}: events out of order");
            }
            let gap_row: Vec<f64> = cuts
                .iter()
                .map(|&cut| length_at(initial, &times, &lengths, cut) as f64 / optimum - 1.0)
                .collect();
            let time_row: Vec<f64> = cuts
                .iter()
                .map(|&cut| {
                    let index = times.partition_point(|&time| time <= cut);
                    if index == 0 { 0.0 } else { times[index - 1] }
                })
                .collect();
            Ok((gap_row, time_row))
        })
        .collect::<anyhow::Result<Vec<_>>>()
        .map(|rows| rows.into_iter().unzip())
}

/// One-sided p-value of "mean difference > 0" by random sign flips.
fn sign_flip_p(differences: &[f64], rng: &mut Stream) -> f64 {
    let observed = mean(differences);
    if observed <= 0.0 {
        return 1.0;
    }
    let count = differences.len() as f64;
    let exceed = (0..FLIPS)
        .filter(|_| {
            let flipped: f64 = differences
                .iter()
                .map(|value| {
                    if rng.next_u64() & 1 == 0 {
                        -value
                    } else {
                        *value
                    }
                })
                .sum::<f64>()
                / count;
            flipped >= observed
        })
        .count();
    (1 + exceed) as f64 / (FLIPS + 1) as f64
}

/// The candidate with the lowest mean gap, and how many candidates share
/// that lowest mean.
///
/// Ties are common once rollouts reach the optimum. A tie goes to the
/// candidate that reached its gap soonest (lowest mean virtual time of its
/// last improvement up to the cut), the faster operator under the time
/// budget; an exact tie on both goes to the earliest candidate in the
/// plan's order, never to the alphabetically first name. `tied` (ties on
/// the gap alone) lets an analysis split or leave out tied states.
fn best_candidate(
    candidates: &[String],
    mean_gap: impl Fn(&str) -> f64,
    mean_reached_ns: impl Fn(&str) -> f64,
) -> (String, usize) {
    let lowest = candidates
        .iter()
        .map(|name| mean_gap(name))
        .fold(f64::INFINITY, f64::min);
    let tied: Vec<&String> = candidates
        .iter()
        .filter(|name| mean_gap(name) == lowest)
        .collect();
    let best = tied
        .iter()
        .copied()
        .reduce(|best, name| {
            if mean_reached_ns(name) < mean_reached_ns(best) {
                name
            } else {
                best
            }
        })
        .expect("at least one candidate");
    (best.to_string(), tied.len())
}

/// Indices of the hypotheses that Holm's step-down procedure rejects at
/// family-wise error `alpha`.
///
/// The k-th smallest p-value (k = 0, 1, …) is compared with
/// `alpha / (m − k)`, stopping at the first that fails. The first step is
/// Bonferroni's test, so "anything rejected" is decided exactly as by
/// Bonferroni; later steps can reject more, never fewer.
fn holm_rejections(p_values: &[f64], alpha: f64) -> Vec<usize> {
    let m = p_values.len();
    let mut order: Vec<usize> = (0..m).collect();
    order.sort_by(|&a, &b| p_values[a].total_cmp(&p_values[b]).then(a.cmp(&b)));
    order
        .into_iter()
        .enumerate()
        .take_while(|&(rank, index)| p_values[index] <= alpha / (m - rank) as f64)
        .map(|(_, index)| index)
        .collect()
}

/// Holm-adjusted p-values, in the input's order: hypothesis `i` is rejected
/// by [`holm_rejections`] at `alpha` exactly when `adjusted[i] <= alpha`.
///
/// The k-th smallest gets `max over j <= k of min(1, (m − j)·p_(j))`, the
/// running maximum keeping the adjusted values in the order of the raw ones.
fn holm_adjusted(p_values: &[f64]) -> Vec<f64> {
    let m = p_values.len();
    let mut order: Vec<usize> = (0..m).collect();
    order.sort_by(|&a, &b| p_values[a].total_cmp(&p_values[b]).then(a.cmp(&b)));
    let mut adjusted = vec![1.0; m];
    let mut running = 0.0f64;
    for (rank, index) in order.into_iter().enumerate() {
        running = running.max(((m - rank) as f64 * p_values[index]).min(1.0));
        adjusted[index] = running;
    }
    adjusted
}

fn paired_interval(differences: &[f64], level: f64, rng: &mut Stream) -> (f64, f64) {
    let count = differences.len();
    let samples: Vec<f64> = (0..BOOTSTRAP)
        .map(|_| {
            (0..count)
                .map(|_| differences[rng.index(count)])
                .sum::<f64>()
                / count as f64
        })
        .collect();
    let tail = (1.0 - level) / 2.0;
    (quantile(&samples, tail), quantile(&samples, 1.0 - tail))
}

/// Mean over the repeats of one parity (even or odd index): one half of
/// the paired repeats, for cross-fitting.
fn half_mean(values: &[f64], parity: usize) -> f64 {
    mean(
        &values
            .iter()
            .enumerate()
            .filter(|(index, _)| index % 2 == parity)
            .map(|(_, value)| *value)
            .collect::<Vec<_>>(),
    )
}

/// The candidate with the lowest `score` on the half `parity`; ties go to
/// the earliest candidate in plan order.
fn lowest_on_half<'a>(
    candidates: &'a [String],
    parity: usize,
    score: impl Fn(&str) -> &'a [f64],
) -> &'a str {
    candidates
        .iter()
        .map(String::as_str)
        .reduce(|best, name| {
            if half_mean(score(name), parity) < half_mean(score(best), parity) {
                name
            } else {
                best
            }
        })
        .expect("candidates exist")
}

/// Regret of the greedy choice with the best candidate chosen on one half
/// of the repeats and measured on the other, averaged over both splits.
fn crossfit_regret(by: &BTreeMap<String, Vec<f64>>, candidates: &[String], greedy: &str) -> f64 {
    let values: Vec<f64> = [(0, 1), (1, 0)]
        .into_iter()
        .map(|(choose, measure)| {
            let best = lowest_on_half(candidates, choose, |name| &by[name]);
            half_mean(&by[greedy], measure) - half_mean(&by[best], measure)
        })
        .collect();
    mean(&values)
}

/// Advantage over greedy of the slice-rate selector, cross-fitted: on one
/// half of the repeats it picks the candidate with the lowest gap after
/// its first slice (the most gain over a whole slice), and its gap at the
/// cut is compared with greedy's on the other half; averaged over both
/// splits. Positive means the selector beats greedy.
fn slice_rate_advantage(
    first_slice: &BTreeMap<String, Vec<f64>>,
    at_cut: &BTreeMap<String, Vec<f64>>,
    candidates: &[String],
    greedy: &str,
) -> f64 {
    let values: Vec<f64> = [(0, 1), (1, 0)]
        .into_iter()
        .map(|(choose, measure)| {
            let pick = lowest_on_half(candidates, choose, |name| &first_slice[name]);
            half_mean(&at_cut[greedy], measure) - half_mean(&at_cut[pick], measure)
        })
        .collect();
    mean(&values)
}

/// For each cut, the label of the first cut at or after it where greedy's
/// mean gap is no worse than the best candidate's mean gap at that cut:
/// how long greedy needs to catch up with the best first operator. `None`
/// if it does not within the horizon.
fn catch_up(labels: &[usize], greedy: &[f64], best: &[f64]) -> Vec<Option<f64>> {
    (0..labels.len())
        .map(|cut| {
            (cut..labels.len())
                .find(|&later| greedy[later] <= best[cut])
                .map(|later| labels[later] as f64)
        })
        .collect()
}

/// Stage `analyse`: arms, states and summary tables, and `summary.md`.
///
/// Whether greedy is beaten is decided per state and horizon by Holm's
/// step-down procedure over the alternatives (family-wise error `ALPHA`);
/// the challenger is the rejected alternative with the largest mean
/// advantage, and its interval is taken at the Bonferroni level
/// `1 − 2·ALPHA/m`, which is conservative for every Holm rejection.
///
/// With `max_candidates = Some(k)`, only the first `k` candidates of each
/// state are analysed (greedy first, then the plan's order). Rollout
/// streams do not depend on the candidate set, so this is exactly the
/// analysis of a run planned with `k` candidates. Its tables go to
/// `analysis-top<k>/` inside the run, leaving the full analysis in place.
///
/// # Errors
///
/// If a result is missing, an instance has no known optimum, a state has
/// fewer than 8 repeats, `k` is 0, or a state's first candidate is not its
/// greedy choice.
pub fn analyse(
    repository: &Path,
    directory: &Path,
    max_candidates: Option<usize>,
) -> anyhow::Result<()> {
    let settings = load_settings(directory)?;
    let plan: Plan = read_json(&directory.join("plan.json"))?;
    let output = match max_candidates {
        None => directory.to_path_buf(),
        Some(0) => bail!("--max-candidates must be at least 1"),
        Some(k) => directory.join(format!("analysis-top{k}")),
    };
    let mut optima: HashMap<String, f64> = HashMap::new();
    for state in &plan.states {
        if !optima.contains_key(&state.instance) {
            let instance = load_instance(repository, &state.path)?;
            optima.insert(
                state.instance.clone(),
                instance
                    .optimum
                    .with_context(|| format!("{} has no known optimum", state.instance))?
                    as f64,
            );
        }
    }
    let run = AnalysisRun {
        directory,
        settings: &settings,
        plan: &plan,
        optima: &optima,
        max_candidates,
    };
    run.write(&output, &CutGrid::Slices(horizons(settings.horizon_slices)))?;
    let times = time_grid(settings.slice_ns_per_city * settings.horizon_slices as f64);
    if times.is_empty() {
        log::info!(
            "analyse: the run's window is shorter than {TIME_GRID_BASE_US} us per city; no time cuts"
        );
        Ok(())
    } else {
        run.write(&output.join("by-time"), &CutGrid::MicrosPerCity(times))
    }
}

/// First cut of the time grid, in microseconds of virtual time per city;
/// the grid doubles from there (10, 20, 40, …): R1's own cut times, which
/// every E1R run of the 2026-11-05 design covers.
const TIME_GRID_BASE_US: usize = 10;

/// The time grid up to a run's window (`window_ns_per_city`, the slice
/// length per city times the horizon in slices).
fn time_grid(window_ns_per_city: f64) -> Vec<usize> {
    let mut grid = Vec::new();
    let mut micros = TIME_GRID_BASE_US;
    // The tolerance keeps a window of exactly 2^k · 10 us.
    while micros as f64 * 1000.0 <= window_ns_per_city * (1.0 + 1e-12) {
        grid.push(micros);
        micros *= 2;
    }
    grid
}

/// Where an analysis cuts the rollouts.
enum CutGrid {
    /// After a number of slices (1, 2, 4, …, the horizon): the run's own
    /// decision points.
    Slices(Vec<usize>),
    /// At fixed virtual times per city (see [`time_grid`]): the same
    /// elapsed time in every run, whatever its slice length, so runs with
    /// different slices can be compared at equal time.
    MicrosPerCity(Vec<usize>),
}

impl CutGrid {
    fn labels(&self) -> &[usize] {
        match self {
            Self::Slices(labels) | Self::MicrosPerCity(labels) => labels,
        }
    }

    /// Cut times of one state (ns of virtual time).
    fn cut_ns(&self, slice_ns_per_city: f64, n: usize) -> Vec<f64> {
        match self {
            Self::Slices(slices) => {
                let slice_ns = slice_ns_per_city * n as f64;
                slices
                    .iter()
                    .map(|&count| count as f64 * slice_ns)
                    .collect()
            }
            Self::MicrosPerCity(micros) => micros
                .iter()
                .map(|&us| us as f64 * 1000.0 * n as f64)
                .collect(),
        }
    }

    /// Name of the cut column in the tables.
    fn column(&self) -> &'static str {
        match self {
            Self::Slices(_) => "slices",
            Self::MicrosPerCity(_) => "us_per_city",
        }
    }

    fn heading(&self) -> &'static str {
        match self {
            Self::Slices(_) => "Horizon (slices)",
            Self::MicrosPerCity(_) => "Virtual time (us per city)",
        }
    }

    /// Label of the analysis random stream; the slice grid keeps the
    /// stream of the analyses before the time grid existed.
    fn stream(&self) -> &'static str {
        match self {
            Self::Slices(_) => "e1r-analyse",
            Self::MicrosPerCity(_) => "e1r-analyse-time",
        }
    }
}

/// What one analysis of a run needs, whatever the cut grid.
struct AnalysisRun<'a> {
    directory: &'a Path,
    settings: &'a Settings,
    plan: &'a Plan,
    optima: &'a HashMap<String, f64>,
    max_candidates: Option<usize>,
}

impl AnalysisRun<'_> {
    /// Arms, states and summary tables and `summary.md` of one cut grid,
    /// written to `output`.
    fn write(&self, output: &Path, grid: &CutGrid) -> anyhow::Result<()> {
        let (directory, settings, plan, optima, max_candidates) = (
            self.directory,
            self.settings,
            self.plan,
            self.optima,
            self.max_candidates,
        );
        std::fs::create_dir_all(output)
            .with_context(|| format!("creating {}", output.display()))?;
        let mut arms: BTreeMap<&str, Vec<String>> = BTreeMap::new();
        let mut arm_n: Vec<u32> = vec![];
        let mut arm_seed: Vec<i64> = vec![];
        let mut arm_slices = vec![];
        let mut arm_mean = vec![];
        let mut arm_sd = vec![];
        let mut arm_rate = vec![];
        let mut arm_holm_p: Vec<Option<f64>> = vec![];
        let mut st: BTreeMap<&str, Vec<String>> = BTreeMap::new();
        let mut st_seed: Vec<i64> = vec![];
        let mut st_candidates: Vec<u32> = vec![];
        let mut st_tied: Vec<u32> = vec![];
        let mut st_slice_rate: Vec<Option<f64>> = vec![];
        let mut st_catch_up: Vec<Option<f64>> = vec![];
        let (
            mut st_slices,
            mut st_greedy_best,
            mut st_regret,
            mut st_cross,
            mut st_beaten,
            mut st_p,
        ) = (vec![], vec![], vec![], vec![], vec![], vec![]);
        let (mut st_adv, mut st_low, mut st_high, mut st_gmean, mut st_bmean) =
            (vec![], vec![], vec![], vec![], vec![]);
        for (index, state) in plan.states.iter().enumerate() {
            let seed = i64::try_from(state.seed).context("state seed out of range")?;
            let candidates = &state.candidates[..max_candidates
                .map_or(state.candidates.len(), |k| k.min(state.candidates.len()))];
            if candidates.first() != Some(&state.greedy) {
                bail!(
                    "{}: the first candidate is not the greedy choice {}",
                    label(state),
                    state.greedy
                );
            }
            let cut_ns = grid.cut_ns(settings.slice_ns_per_city, state.n);
            let mut matrix: BTreeMap<String, Vec<Vec<f64>>> = BTreeMap::new();
            let mut reached: BTreeMap<String, Vec<Vec<f64>>> = BTreeMap::new();
            for name in candidates {
                let op = Op::parse_current(name)?;
                let key = task_key(state, op);
                let (gap_rows, time_rows) =
                    gaps(directory, &key, optima[&state.instance], &cut_ns)?;
                matrix.insert(name.clone(), gap_rows);
                reached.insert(name.clone(), time_rows);
            }
            let repeats = matrix[&state.greedy].len();
            if repeats < MIN_REPEATS {
                bail!(
                    "{}: {repeats} repeats; at least {MIN_REPEATS} needed",
                    label(state)
                );
            }
            let alternatives: Vec<&String> = candidates
                .iter()
                .filter(|name| **name != state.greedy)
                .collect();
            // Bonferroni level for the challenger's interval (Holm decides).
            let level = 1.0 - 2.0 * ALPHA / alternatives.len().max(1) as f64;
            let mut rng = Stream::new(settings.seed, grid.stream(), &[index as u64]);
            // The slice grid's first cut is the first slice: the slice-rate
            // selector's information. The time grid has no such cut.
            let first_slice: Option<BTreeMap<String, Vec<f64>>> =
                matches!(grid, CutGrid::Slices(_)).then(|| {
                    matrix
                        .iter()
                        .map(|(name, rows)| (name.clone(), rows.iter().map(|row| row[0]).collect()))
                        .collect()
                });
            let (mut greedy_means, mut best_means) = (vec![], vec![]);
            for (column, &slices) in grid.labels().iter().enumerate() {
                let at: BTreeMap<String, Vec<f64>> = matrix
                    .iter()
                    .map(|(name, rows)| {
                        (name.clone(), rows.iter().map(|row| row[column]).collect())
                    })
                    .collect();
                let greedy_values = &at[&state.greedy];
                // Every test first: Holm needs all p-values before it decides,
                // and drawing the intervals afterwards keeps the p-values'
                // random stream independent of which alternatives are rejected.
                let differences: Vec<Vec<f64>> = alternatives
                    .iter()
                    .map(|name| {
                        greedy_values
                            .iter()
                            .zip(&at[*name])
                            .map(|(greedy, other)| greedy - other)
                            .collect()
                    })
                    .collect();
                let p_values: Vec<f64> = differences
                    .iter()
                    .map(|values| sign_flip_p(values, &mut rng))
                    .collect();
                let advantages: Vec<f64> = differences.iter().map(|values| mean(values)).collect();
                let adjusted = holm_adjusted(&p_values);
                let smallest = p_values.iter().copied().fold(1.0f64, f64::min);
                // Largest advantage among the rejected; ties go to the earlier
                // candidate in the plan's order.
                let mut chosen: Option<usize> = None;
                for test in holm_rejections(&p_values, ALPHA) {
                    if chosen.is_none_or(|best| {
                        advantages[test] > advantages[best]
                            || (advantages[test] == advantages[best] && test < best)
                    }) {
                        chosen = Some(test);
                    }
                }
                let beaten = chosen.is_some();
                let (challenger, advantage, low, high) = match chosen {
                    Some(test) => {
                        let (low, high) = paired_interval(&differences[test], level, &mut rng);
                        (alternatives[test].clone(), advantages[test], low, high)
                    }
                    None => (String::new(), f64::NAN, f64::NAN, f64::NAN),
                };
                for (name, values) in &at {
                    for (field, value) in [
                        ("instance", state.instance.clone()),
                        ("family", state.family.clone()),
                        ("state", state.state.clone()),
                        ("class", state.class.clone()),
                        ("greedy", state.greedy.clone()),
                        ("candidate", name.clone()),
                    ] {
                        arms.entry(field).or_default().push(value);
                    }
                    arm_n.push(state.n as u32);
                    arm_seed.push(seed);
                    arm_slices.push(slices as u32);
                    let centre = mean(values);
                    arm_mean.push(centre);
                    arm_sd.push(standard_deviation(values));
                    arm_rate.push(state.rates[name]);
                    arm_holm_p.push(
                        alternatives
                            .iter()
                            .position(|other| *other == name)
                            .map(|test| adjusted[test]),
                    );
                }
                let reached_at: BTreeMap<&str, f64> = reached
                    .iter()
                    .map(|(name, rows)| {
                        let times: Vec<f64> = rows.iter().map(|row| row[column]).collect();
                        (name.as_str(), mean(&times))
                    })
                    .collect();
                let (best, tied) =
                    best_candidate(candidates, |name| mean(&at[name]), |name| reached_at[name]);
                st_seed.push(seed);
                st_candidates.push(candidates.len() as u32);
                st_tied.push(tied as u32);
                for (field, value) in [
                    ("instance", state.instance.clone()),
                    ("family", state.family.clone()),
                    ("state", state.state.clone()),
                    ("class", state.class.clone()),
                    ("greedy", state.greedy.clone()),
                    ("best", best.clone()),
                    ("challenger", challenger),
                ] {
                    st.entry(field).or_default().push(value);
                }
                st_slices.push(slices as u32);
                st_greedy_best.push(best == state.greedy);
                st_gmean.push(mean(greedy_values));
                st_bmean.push(mean(&at[&best]));
                st_regret.push(mean(greedy_values) - mean(&at[&best]));
                st_cross.push(crossfit_regret(&at, candidates, &state.greedy));
                st_slice_rate.push(
                    first_slice
                        .as_ref()
                        .map(|first| slice_rate_advantage(first, &at, candidates, &state.greedy)),
                );
                greedy_means.push(mean(greedy_values));
                best_means.push(mean(&at[&best]));
                st_beaten.push(beaten);
                st_p.push(smallest);
                st_adv.push(advantage);
                st_low.push(low);
                st_high.push(high);
            }
            st_catch_up.extend(catch_up(grid.labels(), &greedy_means, &best_means));
        }
        // Summary per (grouping, group, horizon).
        let mut groups: BTreeMap<(String, String, u32), Vec<usize>> = BTreeMap::new();
        for row in 0..st_slices.len() {
            groups
                .entry(("class".into(), st["class"][row].clone(), st_slices[row]))
                .or_default()
                .push(row);
            groups
                .entry(("family".into(), st["family"][row].clone(), st_slices[row]))
                .or_default()
                .push(row);
            groups
                .entry(("all".into(), "all".into(), st_slices[row]))
                .or_default()
                .push(row);
        }
        let scope = match max_candidates {
            None => "every planned candidate".to_string(),
            Some(k) => format!("the first {k} candidates of each state (greedy first)"),
        };
        let heading = grid.heading();
        let mut md = format!(
            "# E1R summary\n\nCandidates analysed: {scope}. Greedy beaten: Holm's step-down \
         procedure over the alternatives, family-wise error {ALPHA} per state and cut.\n\n\
         | Grouping | Group | {heading} | States | Greedy beaten | Greedy best | Median regret (cross-fitted) |\n|---|---|---|---|---|---|---|\n",
        );
        let (
            mut g_grouping,
            mut g_group,
            mut g_slices,
            mut g_states,
            mut g_beaten,
            mut g_best,
            mut g_regret,
            mut g_cross,
        ) = (
            vec![],
            vec![],
            vec![],
            vec![],
            vec![],
            vec![],
            vec![],
            vec![],
        );
        for ((grouping, group, slices), rows) in &groups {
            let count = rows.len() as f64;
            let beaten = rows.iter().filter(|&&row| st_beaten[row]).count() as f64 / count;
            let best = rows.iter().filter(|&&row| st_greedy_best[row]).count() as f64 / count;
            let regret = median(&rows.iter().map(|&row| st_regret[row]).collect::<Vec<_>>());
            let cross = median(&rows.iter().map(|&row| st_cross[row]).collect::<Vec<_>>());
            if grouping != "family" {
                md += &format!(
                    "| {grouping} | {group} | {slices} | {} | {:.0}% | {:.0}% | {:+.4}% |\n",
                    rows.len(),
                    beaten * 100.0,
                    best * 100.0,
                    cross * 100.0
                );
            }
            g_grouping.push(grouping.clone());
            g_group.push(group.clone());
            g_slices.push(*slices);
            g_states.push(rows.len() as u32);
            g_beaten.push(beaten);
            g_best.push(best);
            g_regret.push(regret);
            g_cross.push(cross);
        }
        let mut arms_table = Table::new();
        for (field, values) in arms {
            arms_table = arms_table.with(field, Column::Str(values));
        }
        write(
            &output.join("arms.parquet"),
            arms_table
                .with("seed", Column::I64(arm_seed))
                .with("n", Column::U32(arm_n))
                .with(grid.column(), Column::U32(arm_slices))
                .with("mean_gap", Column::F64(arm_mean))
                .with("sd_gap", Column::F64(arm_sd))
                .with("measured_rate", Column::F64(arm_rate))
                .with("holm_p", Column::OptF64(arm_holm_p)),
        )?;
        let mut states_table = Table::new();
        for (field, values) in st {
            states_table = states_table.with(field, Column::Str(values));
        }
        write(
            &output.join("states.parquet"),
            states_table
                .with("seed", Column::I64(st_seed))
                .with("candidates", Column::U32(st_candidates))
                .with("tied_best", Column::U32(st_tied))
                .with("slice_rate_advantage", Column::OptF64(st_slice_rate))
                .with("greedy_catch_up", Column::OptF64(st_catch_up))
                .with(grid.column(), Column::U32(st_slices))
                .with("greedy_is_best", Column::Bool(st_greedy_best))
                .with("greedy_mean_gap", Column::F64(st_gmean))
                .with("best_mean_gap", Column::F64(st_bmean))
                .with("regret", Column::F64(st_regret))
                .with("regret_crossfit", Column::F64(st_cross))
                .with("beaten", Column::Bool(st_beaten))
                .with("smallest_p", Column::F64(st_p))
                .with(
                    "advantage",
                    Column::OptF64(
                        st_adv
                            .into_iter()
                            .map(|v| (!v.is_nan()).then_some(v))
                            .collect(),
                    ),
                )
                .with(
                    "advantage_low",
                    Column::OptF64(
                        st_low
                            .into_iter()
                            .map(|v| (!v.is_nan()).then_some(v))
                            .collect(),
                    ),
                )
                .with(
                    "advantage_high",
                    Column::OptF64(
                        st_high
                            .into_iter()
                            .map(|v| (!v.is_nan()).then_some(v))
                            .collect(),
                    ),
                ),
        )?;
        write(
            &output.join("summary.parquet"),
            Table::new()
                .with("grouping", Column::Str(g_grouping))
                .with("group", Column::Str(g_group))
                .with(grid.column(), Column::U32(g_slices))
                .with("states", Column::U32(g_states))
                .with("beaten_share", Column::F64(g_beaten))
                .with("greedy_best_share", Column::F64(g_best))
                .with("median_regret", Column::F64(g_regret))
                .with("median_regret_crossfit", Column::F64(g_cross)),
        )?;
        write_atomic(&output.join("summary.md"), md.as_bytes())?;
        log::info!("analysis written to {}", output.display());
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(left: &[f64], right: &[f64]) -> bool {
        left.len() == right.len() && left.iter().zip(right).all(|(a, b)| (a - b).abs() < 1e-12)
    }

    #[test]
    fn ties_go_to_the_faster_then_to_plan_order() {
        // Plan order deliberately differs from alphabetical order.
        let order = ["two_opt_nl", "kick", "double_bridge"];
        let names: Vec<String> = order.map(String::from).to_vec();
        let by = |values: [f64; 3]| {
            move |name: &str| values[order.iter().position(|other| *other == name).unwrap()]
        };
        // All tied on the gap: the one that got there first wins.
        assert_eq!(
            best_candidate(&names, by([0.0, 0.0, 0.0]), by([9.0, 4.0, 5.0])),
            ("kick".to_string(), 3)
        );
        // Tied on gap and time: the earliest in plan order (kick), not the
        // alphabetically first (double_bridge).
        assert_eq!(
            best_candidate(&names, by([0.5, 0.1, 0.1]), by([1.0, 2.0, 2.0])),
            ("kick".to_string(), 2)
        );
        // No tie: the time does not matter.
        assert_eq!(
            best_candidate(&names, by([0.5, 0.2, 0.1]), by([1.0, 1.0, 9.0])),
            ("double_bridge".to_string(), 1)
        );
    }

    #[test]
    fn time_grid_covers_each_window_and_matches_r1() {
        // R1 (0.01 ms x 64) and R2 (0.001 ms x 640) share 10..640 us per
        // city; R3 (0.05 ms x 64) reaches 2560.
        let shared = vec![10, 20, 40, 80, 160, 320, 640];
        assert_eq!(time_grid(10_000.0 * 64.0), shared);
        assert_eq!(time_grid(1_000.0 * 640.0), shared);
        assert_eq!(time_grid(50_000.0 * 64.0).last(), Some(&2560));
        assert!(time_grid(9_000.0).is_empty());
        // On the slice grid of R1, cut k is 2^k slices, the same time as
        // time cut k: the two R1 analyses cut at identical instants.
        let slices = CutGrid::Slices(horizons(64)).cut_ns(10_000.0, 100);
        let micros = CutGrid::MicrosPerCity(shared).cut_ns(10_000.0, 100);
        assert_eq!(slices, micros);
    }

    #[test]
    fn catch_up_finds_the_first_cut_where_greedy_reaches_the_best() {
        // Cuts 1, 2, 4, 8 slices; greedy reaches the best's gap at cut 1
        // (0.5) only at cut 4, the best's gap at cut 2 (0.3) at cut 8, and
        // never the best's 0.0 at cuts 4 and 8.
        let labels = [1, 2, 4, 8];
        let greedy = [0.9, 0.6, 0.5, 0.3];
        let best = [0.5, 0.3, 0.0, 0.0];
        assert_eq!(
            catch_up(&labels, &greedy, &best),
            vec![Some(4.0), Some(8.0), None, None]
        );
    }

    #[test]
    fn slice_rate_selector_is_cross_fitted() {
        // Two candidates, four repeats. On the first slice "fast" leads on
        // both halves, so it is picked on each; at the cut it is 0.1 better
        // than greedy on every repeat.
        let names = vec!["greedy".to_string(), "fast".to_string()];
        let first: BTreeMap<String, Vec<f64>> = [
            ("greedy".to_string(), vec![0.5; 4]),
            ("fast".to_string(), vec![0.4; 4]),
        ]
        .into();
        let cut: BTreeMap<String, Vec<f64>> = [
            ("greedy".to_string(), vec![0.3; 4]),
            ("fast".to_string(), vec![0.2; 4]),
        ]
        .into();
        assert!((slice_rate_advantage(&first, &cut, &names, "greedy") - 0.1).abs() < 1e-12);
        // If greedy also leads the first slice, the selector picks greedy.
        assert_eq!(slice_rate_advantage(&cut, &cut, &names, "fast"), 0.0);
    }

    #[test]
    fn holm_rejects_beyond_bonferroni() {
        // Thresholds 0.0125, 0.0167, 0.025, 0.05 against 0.004, 0.012,
        // 0.02, 0.2: the first three pass, the fourth stops the procedure.
        // Bonferroni (0.0125 for all) would reject only indices 3 and 0.
        let p_values = [0.012, 0.02, 0.2, 0.004];
        assert_eq!(holm_rejections(&p_values, 0.05), vec![3, 0, 1]);
        assert!(close(&holm_adjusted(&p_values), &[0.036, 0.04, 0.2, 0.016]));
    }

    #[test]
    fn holm_stops_at_the_first_failure() {
        // 0.03 > 0.05 / 2, so nothing is rejected although 0.04 < 0.05.
        assert!(holm_rejections(&[0.03, 0.04], 0.05).is_empty());
        assert!(holm_rejections(&[], 0.05).is_empty());
    }

    #[test]
    fn holm_adjusted_is_monotone_and_matches_rejections() {
        // 2 × 0.01 = 0.02 exceeds 1 × 0.011, so the running maximum lifts it.
        assert!(close(&holm_adjusted(&[0.01, 0.011]), &[0.02, 0.02]));
        let p_values = [0.3, 0.001, 0.02, 0.011, 0.6, 0.009];
        let adjusted = holm_adjusted(&p_values);
        for alpha in [0.01, 0.05, 0.1] {
            let mut rejected = holm_rejections(&p_values, alpha);
            rejected.sort_unstable();
            let by_adjusted: Vec<usize> = (0..p_values.len())
                .filter(|&index| adjusted[index] <= alpha)
                .collect();
            assert_eq!(rejected, by_adjusted, "alpha {alpha}");
        }
    }

    #[test]
    fn smaller_draws_are_prefixes_of_larger_ones() {
        let pools = [5, 2, 8, 1, 6, 0, 3];
        let mut few = Stream::new(0, "e1r-plan", &[]);
        let mut many = Stream::new(0, "e1r-plan", &[]);
        for population in pools {
            let small = nested_draw(&mut few, population, 3);
            let large = nested_draw(&mut many, population, 6);
            assert_eq!(small.len(), population.min(3));
            assert_eq!(large.len(), population.min(6));
            assert_eq!(small, large[..small.len()], "population {population}");
        }
    }
}
