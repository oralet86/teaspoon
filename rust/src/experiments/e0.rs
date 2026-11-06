//! E0-rs: can the Rust clock be trusted, and what does everything cost?
//! (`ideas/rust-port.md` §8.1).
//!
//! One run answers seven questions, in this order, all on generated uniform
//! instances ([`super::uniform_instance`]):
//!
//! 1. **Clock:** TSC calibration at start and end, read overhead and
//!    resolution.
//! 4. **Distance-read cost** against n: matrix (independent random reads,
//!    a dependent chain, row-sequential), candidate cache, computed
//!    `EUC_2D` and `GEO`.
//! 5. **Flip cost** of both tour layouts against n (random and short
//!    reversals), for the representation threshold `n_split`.
//! 2. **Cost per application** of every operator on three states (random
//!    tour, O7 optimum, O18 optimum), both layouts, with repeats
//!    interleaved over the sweep, timed in measurement A's passes (per
//!    call, batched, restore batch); the repeatability gate is
//!    judged on the spread of the batched time between repeats, which
//!    replay identical applications.
//! 7. **Per-call against batched timing:** on the O18 optimum, at anchors
//!    where no deterministic operator moves, each deterministic operator's
//!    mean time per application timed per call (interleaved with every
//!    operator, and grouped by operator) and as one batch; the ratios show
//!    what per-call clock reads add to each operator.
//! 6. **Full O7 descents** from random tours, both layouts.
//! 3. **Contention gate:** identical rounds of applications on 1, 2, 4, …
//!    pinned workers; a worker count passes if no operator slows down by
//!    more than 2% against one worker.
//!
//! The cost sweep runs each operator over all its anchors back to back,
//! as measurement A's per-call pass does.  Flips are drawn first and then
//! replayed as one timed region.  The core frequency of every cost block
//! is checked against a reference with the core-cycle counter.
//!
//! Untimed work (instances, alpha candidates, states) is done first, on
//! every CPU; timed work runs on pinned threads afterwards and never
//! overlaps it.  Tables are Parquet files in `runs/e0-rs/<timestamp>/`;
//! `summary.json` and `summary.md` hold the gate verdicts and the
//! summary tables.

use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};
use std::sync::Barrier;

use anyhow::{Context, bail};
use serde::Serialize;
use serde_json::{Value, json};

use super::common::{
    Pass, batched_counts, batched_time, improving_replays, sampled_batch, timed_restores, warm_up,
};
use super::{
    coefficient_of_variation, cv_interval, mean, median, quantile, random_order, uniform_instance,
};
use crate::City;
use crate::instance::Instance;
use crate::instance::neighbours::CandidateLists;
use crate::instance::storage::{Computed, Dist, Matrix};
use crate::instance::tsplib::CoordinateKind;
use crate::io::parquet::{Column, Table, write};
use crate::io::provenance::write_config;
use crate::io::rundir::{new_run_directory, write_atomic};
use crate::ops::{Op, Problem, Work, descend};
use crate::rng::Stream;
use crate::timing::counters::Counts;
use crate::timing::cycles::{self, Reading, ThreadCounters};
use crate::timing::machine::{self, MachineState};
use crate::timing::pin;
use crate::timing::tsc::{self, Clock};
use crate::tour::array::Array;
use crate::tour::blocks::Blocks;
use crate::tour::{Layout, Tour};

/// Repeatability gate: the largest coefficient of variation between
/// repeats allowed for any one cell (n, layout, state, operator).  Until
/// 2026-11-02 the gate was over all cells (median ≤ 1.5%, 95th percentile
/// ≤ 5%), which let the steadiest cells carry the worst 5%.
pub const GATE_CELL_CV: f64 = 0.05;
/// Contention gate: largest allowed slowdown of any operator (spec §13 O5).
pub const GATE_SLOWDOWN: f64 = 0.02;
/// Largest allowed change of the measured core frequency within a block
/// (spec §13 O5); blocks beyond it are flagged as suspect.
pub const FREQUENCY_DRIFT: f64 = 0.01;

/// Settings of one E0-rs run.
#[derive(Debug, Clone, Serialize)]
pub struct Settings {
    /// Seed of instances, states, anchors and random streams.
    pub seed: u64,
    /// Sizes of the operator-cost sweep and the descents.
    pub cost_sizes: Vec<usize>,
    /// The random-tour state is measured only up to this size (as the main
    /// run skips early states above n = 1000).
    pub random_state_max_n: usize,
    /// Anchors per state in the cost sweep (a fixed sample).
    pub anchors: usize,
    /// Interleaved repeats of the cost sweep.
    pub repeats: usize,
    /// Rounds over every operator within each repeat of a cost block; a
    /// repeat's time of an operator is the median over its rounds, as
    /// measurement A takes the median over its rounds of a state.
    pub rounds: usize,
    /// Instance sizes of the contention gate.
    pub contention_sizes: Vec<usize>,
    /// Worker count of each contention phase, in order.
    pub contention_workers: Vec<usize>,
    /// Length of each contention phase with several workers.
    pub contention_seconds: f64,
    /// Length of the opening and closing single-worker phases, longer than
    /// the others: the drift gate compares just these two, and at 120 s
    /// they gave 6 rounds per cell, too few to pin the slowest operators
    /// (about 26 µs per application at n = 5000, single rounds 5–10% slow
    /// in bursts) within the 2% gate.
    pub contention_single_seconds: f64,
    /// Anchors of each batch of the contention gate (per state, operator
    /// and round; measurement A's batches have 32–36).
    pub contention_anchors: usize,
    /// Sizes of the distance-read measurement.
    pub read_sizes: Vec<usize>,
    /// Reads per distance-read measurement.
    pub reads: usize,
    /// Repeats of every distance-read and flip measurement.
    pub read_repeats: usize,
    /// Sizes of the flip measurement.
    pub flip_sizes: Vec<usize>,
    /// Flips per flip measurement.
    pub flips: usize,
    /// Path length bound of the "short" flips.
    pub short_flip: usize,
    /// Full O7 descents per size and layout.
    pub descent_repeats: usize,
}

impl Settings {
    /// The full run.
    pub fn full() -> Self {
        Self {
            seed: 0,
            cost_sizes: vec![100, 200, 500, 1000, 2000, 5000],
            random_state_max_n: 1000,
            anchors: 100,
            repeats: 5,
            rounds: 3,
            contention_sizes: vec![1000, 5000],
            contention_workers: vec![1, 2, 4, 6, 7, 1],
            contention_seconds: 120.0,
            contention_single_seconds: 300.0,
            contention_anchors: 32,
            read_sizes: vec![100, 200, 500, 1000, 2000, 5000, 10000, 12000],
            reads: 1 << 22,
            read_repeats: 5,
            flip_sizes: vec![100, 200, 500, 1000, 2000, 5000, 10000, 20000],
            flips: 20_000,
            short_flip: 50,
            descent_repeats: 10,
        }
    }

    /// A dry run of a few seconds that exercises every step.
    pub fn quick() -> Self {
        Self {
            seed: 0,
            cost_sizes: vec![100, 300],
            random_state_max_n: 1000,
            anchors: 8,
            repeats: 2,
            rounds: 2,
            contention_sizes: vec![300],
            contention_workers: vec![1, 2, 1],
            contention_seconds: 2.0,
            contention_single_seconds: 3.0,
            contention_anchors: 4,
            read_sizes: vec![100, 1000],
            reads: 1 << 16,
            read_repeats: 2,
            flip_sizes: vec![100, 1000],
            flips: 1000,
            short_flip: 50,
            descent_repeats: 2,
        }
    }

    fn state_names(&self, n: usize) -> Vec<&'static str> {
        if n <= self.random_state_max_n {
            vec!["random", "opt2", "lkopt"]
        } else {
            vec!["opt2", "lkopt"]
        }
    }
}

/// One generated instance and its measured states (name, order).
struct StateSet {
    instance: Instance,
    states: Vec<(&'static str, Vec<City>)>,
}

impl StateSet {
    fn n(&self) -> usize {
        self.instance.n()
    }

    fn problem(&self) -> Problem<'_, Matrix, true> {
        Problem {
            dist: &self.instance.matrix,
            neighbours: &self.instance.neighbours,
            alpha: self.instance.alpha.as_ref(),
        }
    }
}

/// Builds the instance of size `n` and its states: a random tour, its O7
/// descent, and an O18 optimum reached from the O7 optimum.
fn build_states(settings: &Settings, n: usize) -> anyhow::Result<StateSet> {
    let instance = uniform_instance(n, settings.seed, true)?;
    let problem = Problem::<'_, Matrix, true> {
        dist: &instance.matrix,
        neighbours: &instance.neighbours,
        alpha: instance.alpha.as_ref(),
    };
    let random = random_order(n, settings.seed, "e0-state");
    let mut tour: Tour<Array> = Tour::new(&random, &instance.matrix);
    let all: Vec<City> = (0..n as City).collect();
    let mut work = Work::default();
    descend(&mut tour, &problem, &mut work, &all, None);
    tour.clear_log();
    let opt2 = tour.order();
    let mut rng = Stream::new(settings.seed, "e0-lkopt", &[]);
    loop {
        let mut improved = false;
        for &city in &all {
            let application =
                Op::LinKernighanAlpha.apply(&mut tour, &problem, &mut rng, &mut work, Some(city));
            improved |= application.delta < 0;
            tour.clear_log();
        }
        if !improved {
            break;
        }
    }
    tour.check(&instance.matrix)
        .map_err(|message| anyhow::anyhow!("lkopt state of n={n}: {message}"))?;
    let lkopt = tour.order();
    let states = settings
        .state_names(n)
        .into_iter()
        .map(|name| {
            let order = match name {
                "random" => random.clone(),
                "opt2" => opt2.clone(),
                _ => lkopt.clone(),
            };
            (name, order)
        })
        .collect();
    Ok(StateSet { instance, states })
}

/// Runs `job` on a new thread pinned to `cpu` and returns its result.
fn on_core<T: Send>(
    cpu: usize,
    job: impl FnOnce() -> anyhow::Result<T> + Send,
) -> anyhow::Result<T> {
    std::thread::scope(|scope| {
        scope
            .spawn(move || {
                pin::pin_current_thread(cpu)?;
                job()
            })
            .join()
            .map_err(|_| anyhow::anyhow!("timed thread on cpu {cpu} panicked"))?
    })
}

/// Periodic readings of the machine state (spec §7.2).
#[derive(Default)]
struct MachineLog {
    label: Vec<String>,
    monotonic_ns: Vec<i64>,
    cpu: Vec<u32>,
    khz: Vec<Option<f64>>,
    governor: Vec<String>,
    boost: Vec<String>,
    temperature_c: Vec<Option<f64>>,
    fclk_mhz: Vec<Option<f64>>,
    mclk_mhz: Vec<Option<f64>>,
}

impl MachineLog {
    fn record(&mut self, label: &str, cpus: &[usize]) {
        let state = MachineState::read(cpus);
        for ((cpu, khz), (_, governor)) in state.average_khz.iter().zip(&state.governors) {
            self.label.push(label.to_string());
            self.monotonic_ns.push(state.monotonic_ns as i64);
            self.cpu.push(*cpu as u32);
            self.khz.push(khz.map(|value| value as f64));
            self.governor.push(governor.clone().unwrap_or_default());
            self.boost.push(format!("{:?}", state.boost));
            self.temperature_c.push(state.temperature_c);
            self.fclk_mhz.push(state.fclk_mhz.map(f64::from));
            self.mclk_mhz.push(state.mclk_mhz.map(f64::from));
        }
    }

    fn into_table(self) -> Table {
        Table::new()
            .with("label", Column::Str(self.label))
            .with("monotonic_ns", Column::I64(self.monotonic_ns))
            .with("cpu", Column::U32(self.cpu))
            .with("average_khz", Column::OptF64(self.khz))
            .with("governor", Column::Str(self.governor))
            .with("boost", Column::Str(self.boost))
            .with("temperature_c", Column::OptF64(self.temperature_c))
            .with("fclk_mhz", Column::OptF64(self.fclk_mhz))
            .with("mclk_mhz", Column::OptF64(self.mclk_mhz))
    }
}

/// Longest idle wait before the closing contention phase (s).
const COOL_DOWN_SECONDS: u64 = 180;
/// The closing phase starts once the package is this close to the opening
/// phase's temperature (°C).
const COOL_DOWN_KELVIN: f64 = 3.0;

/// Waits, idle, until the package temperature is back within
/// [`COOL_DOWN_KELVIN`] of `opening` or [`COOL_DOWN_SECONDS`] have passed.
/// The closing single-worker phase checks whether the machine drifted over
/// the gate; straight after the many-worker phases it also measured their
/// after-effects (2026-11-02: up to 28% slower memory-bound cells at
/// n = 5000, steady over the whole phase).
fn cool_down(opening: Option<f64>) {
    let Some(opening) = opening else {
        return;
    };
    let started = std::time::Instant::now();
    while started.elapsed().as_secs() < COOL_DOWN_SECONDS {
        if machine::temperature_c().is_some_and(|now| now <= opening + COOL_DOWN_KELVIN) {
            break;
        }
        std::thread::sleep(std::time::Duration::from_secs(5));
    }
    log::info!(
        "cool-down before the closing phase: {:.0} s, {:?} °C (opening {opening} °C)",
        started.elapsed().as_secs_f64(),
        machine::temperature_c()
    );
}

fn khz(cpu: usize) -> Option<f64> {
    machine::average_khz(cpu).map(|value| value as f64)
}

fn drifted(before: Option<f64>, after: Option<f64>) -> bool {
    match (before, after) {
        (Some(before), Some(after)) => (after - before).abs() / before > FREQUENCY_DRIFT,
        _ => false,
    }
}

// ---------------------------------------------------------------- 1. clock

/// Clock readings at one point of the run.
#[derive(Debug, Clone, Serialize)]
struct ClockReading {
    stage: &'static str,
    clock: Clock,
    overhead_p05_ticks: f64,
    overhead_p95_ticks: f64,
    overhead_p99_ticks: f64,
    resolution_ticks: u64,
}

fn read_clock(stage: &'static str) -> anyhow::Result<ClockReading> {
    let clock = Clock::calibrate(10, 100)?;
    let gaps: Vec<f64> = (0..100_000)
        .map(|_| {
            let begin = tsc::start();
            (tsc::stop() - begin) as f64
        })
        .collect();
    let mut resolution = u64::MAX;
    let mut previous = tsc::start();
    for _ in 0..100_000 {
        let now = tsc::start();
        if now > previous {
            resolution = resolution.min(now - previous);
        }
        previous = now;
    }
    Ok(ClockReading {
        stage,
        clock,
        overhead_p05_ticks: quantile(&gaps, 0.05),
        overhead_p95_ticks: quantile(&gaps, 0.95),
        overhead_p99_ticks: quantile(&gaps, 0.99),
        resolution_ticks: resolution,
    })
}

// ------------------------------------------------------- 4. distance reads

/// xorshift64: the cheapest generator that keeps indices unpredictable to
/// the prefetcher.
#[inline(always)]
fn xorshift(mut x: u64) -> u64 {
    x ^= x << 13;
    x ^= x >> 7;
    x ^ (x << 17)
}

/// A city in `0..n` from 64 random bits (multiply-shift).
#[inline(always)]
fn city_below(x: u64, n: usize) -> City {
    ((u128::from(x) * n as u128) >> 64) as City
}

/// A pair of cities from one 64-bit draw.
#[inline(always)]
fn city_pair(x: u64, n: usize) -> (City, City) {
    (
        city_below(x, n),
        city_below(x.wrapping_mul(0x9E37_79B9_7F4A_7C15), n),
    )
}

/// One kind of distance read, given 64 random bits.  The baselines do the
/// same index arithmetic without touching memory, so a read's own cost is
/// its time minus its baseline's.
trait Reader {
    fn read(&self, x: u64) -> u64;
}

/// Two indices, no memory access (baseline of matrix and computed reads).
struct PairBaseline(usize);

impl Reader for PairBaseline {
    #[inline(always)]
    fn read(&self, x: u64) -> u64 {
        let (i, j) = city_pair(x, self.0);
        u64::from(i ^ j)
    }
}

/// A (city, city) distance from any storage.
struct DistReader<'a, D: Dist>(&'a D);

impl<D: Dist> Reader for DistReader<'_, D> {
    #[inline(always)]
    fn read(&self, x: u64) -> u64 {
        let (i, j) = city_pair(x, self.0.n());
        self.0.d(i, j) as u64
    }
}

/// A city and a candidate slot, no memory access (baseline of cache reads).
struct SlotBaseline(usize, usize);

impl Reader for SlotBaseline {
    #[inline(always)]
    fn read(&self, x: u64) -> u64 {
        u64::from(city_below(x, self.0)) ^ ((x >> 3) % self.1 as u64)
    }
}

/// A (city, candidate) distance from the candidate cache.
struct CacheReader<'a>(&'a CandidateLists, usize);

impl Reader for CacheReader<'_> {
    #[inline(always)]
    fn read(&self, x: u64) -> u64 {
        let city = city_below(x, self.1);
        let slot = ((x >> 3) % self.0.k() as u64) as usize;
        self.0.distances(city)[slot] as u64
    }
}

/// `reads` reads; with `CHAIN`, each read's indices depend on the previous
/// value (latency), otherwise they are independent (throughput).
#[inline(never)]
fn read_loop<R: Reader, const CHAIN: bool>(reader: &R, reads: usize, seed: u64) -> u64 {
    let mut x = seed;
    let mut value = 0u64;
    let mut sum = 0u64;
    for _ in 0..reads {
        x = xorshift(if CHAIN { x ^ value } else { x });
        value = reader.read(x);
        sum = sum.wrapping_add(value);
    }
    sum
}

#[inline(never)]
fn reads_rows(matrix: &Matrix, reads: usize, seed: u64) -> u64 {
    let n = matrix.n();
    let mut x = seed;
    let mut sum = 0u64;
    for _ in 0..reads.div_ceil(n) {
        x = xorshift(x);
        let row = matrix.row(city_below(x, n));
        sum = sum.wrapping_add(row.iter().map(|&value| value as u64).sum::<u64>());
    }
    sum
}

#[derive(Default)]
struct ReadRows {
    n: Vec<u32>,
    mode: Vec<String>,
    access: Vec<String>,
    repeat: Vec<u32>,
    reads: Vec<i64>,
    ns_per_read: Vec<f64>,
}

/// A measurement: name, its baseline's name, and the loop for a seed and
/// access pattern (`true` = chain).
type Measure<'a> = (
    &'static str,
    &'static str,
    Box<dyn Fn(u64, bool) -> u64 + 'a>,
);

fn measure_reads(
    settings: &Settings,
    clock: &Clock,
    log: &mut MachineLog,
    cpu: usize,
) -> anyhow::Result<ReadRows> {
    let mut rows = ReadRows::default();
    let reads = settings.reads;
    for &n in &settings.read_sizes {
        log.record(&format!("reads n={n}"), &[cpu]);
        let instance = uniform_instance(n, settings.seed, false)?;
        let mut rng = Stream::new(settings.seed, "e0-geo", &[n as u64]);
        let geo = Computed::new(
            CoordinateKind::Geo,
            (0..n)
                .map(|_| [(rng.unit() - 0.5) * 2.8, (rng.unit() - 0.5) * 6.2])
                .collect(),
        );
        let euc = instance
            .computed
            .as_ref()
            .context("uniform instances have coordinates")?;
        let k = instance.neighbours.k();
        let pair = PairBaseline(n);
        let slot = SlotBaseline(n, k);
        let matrix = DistReader(&instance.matrix);
        let euc = DistReader(euc);
        let geo = DistReader(&geo);
        let cache = CacheReader(&instance.neighbours, n);
        fn both<R: Reader>(reader: &R, reads: usize) -> impl Fn(u64, bool) -> u64 + '_ {
            move |seed, chain| {
                if chain {
                    read_loop::<R, true>(reader, reads, seed)
                } else {
                    read_loop::<R, false>(reader, reads, seed)
                }
            }
        }
        let measures: Vec<Measure<'_>> = vec![
            ("pair_baseline", "", Box::new(both(&pair, reads))),
            ("matrix", "pair_baseline", Box::new(both(&matrix, reads))),
            (
                "computed_euc2d",
                "pair_baseline",
                Box::new(both(&euc, reads)),
            ),
            ("computed_geo", "pair_baseline", Box::new(both(&geo, reads))),
            ("slot_baseline", "", Box::new(both(&slot, reads))),
            ("cache", "slot_baseline", Box::new(both(&cache, reads))),
        ];
        for repeat in 0..settings.read_repeats {
            let seed = 0x9E37_79B9 ^ ((repeat as u64 + 1) * 0x1000_0001);
            for (mode, _, measure) in &measures {
                for chain in [false, true] {
                    let begin = tsc::start();
                    let sum = measure(seed, chain);
                    let end = tsc::stop();
                    std::hint::black_box(sum);
                    rows.n.push(n as u32);
                    rows.mode.push((*mode).to_string());
                    rows.access
                        .push(if chain { "chain" } else { "independent" }.to_string());
                    rows.repeat.push(repeat as u32);
                    rows.reads.push(reads as i64);
                    rows.ns_per_read
                        .push(clock.region_ns(begin, end) / reads as f64);
                }
            }
            let begin = tsc::start();
            let sum = reads_rows(&instance.matrix, reads, seed);
            let end = tsc::stop();
            std::hint::black_box(sum);
            let count = reads.div_ceil(n) * n;
            rows.n.push(n as u32);
            rows.mode.push("matrix_rows".to_string());
            rows.access.push("sequential".to_string());
            rows.repeat.push(repeat as u32);
            rows.reads.push(count as i64);
            rows.ns_per_read
                .push(clock.region_ns(begin, end) / count as f64);
        }
    }
    Ok(rows)
}

/// Baseline of each read mode (empty: none).
fn read_baseline(mode: &str) -> Option<&'static str> {
    match mode {
        "matrix" | "computed_euc2d" | "computed_geo" => Some("pair_baseline"),
        "cache" => Some("slot_baseline"),
        _ => None,
    }
}

// ---------------------------------------------------------------- 5. flips

#[derive(Default)]
struct FlipRows {
    n: Vec<u32>,
    layout: Vec<String>,
    mode: Vec<String>,
    repeat: Vec<u32>,
    flips: Vec<i64>,
    ns_per_flip: Vec<f64>,
    positions_per_flip: Vec<f64>,
}

/// Times `settings.flips` reversals on one layout as one region: the
/// flips are first drawn on a copy of the layout (a short flip's end
/// depends on the tour at that moment), then replayed, timed, on the
/// layout itself; both must end identical.
fn flip_block<L: Layout>(
    settings: &Settings,
    clock: &Clock,
    n: usize,
    short: bool,
    repeat: usize,
    rows: &mut FlipRows,
) -> anyhow::Result<()> {
    let order = random_order(n, settings.seed, "e0-flips");
    let mut layout = L::from_order(&order);
    let mut draft = layout.clone();
    let mut rng = Stream::new(
        settings.seed,
        "e0-flip-draws",
        &[n as u64, u64::from(short), repeat as u64],
    );
    let mut flips: Vec<(City, City)> = Vec::with_capacity(settings.flips);
    while flips.len() < settings.flips {
        let a = rng.index(n) as City;
        let b = if short {
            draft.walk(a, 1 + rng.index(settings.short_flip.min(n - 2)))
        } else {
            rng.index(n) as City
        };
        if a == b || draft.next(b) == a {
            continue;
        }
        draft.reverse(a, b);
        flips.push((a, b));
    }
    let mut positions = 0u64;
    let begin = tsc::start();
    for &(a, b) in &flips {
        positions += layout.reverse(a, b);
    }
    let end = tsc::stop();
    std::hint::black_box(positions);
    if (0..n as City).any(|city| layout.next(city) != draft.next(city)) {
        bail!(
            "flip replay on {} (n={n}) did not reproduce its draft",
            L::NAME
        );
    }
    rows.n.push(n as u32);
    rows.layout.push(L::NAME.to_string());
    rows.mode
        .push(if short { "short" } else { "random" }.to_string());
    rows.repeat.push(repeat as u32);
    rows.flips.push(flips.len() as i64);
    rows.ns_per_flip
        .push(clock.region_ns(begin, end) / flips.len() as f64);
    rows.positions_per_flip
        .push(positions as f64 / flips.len() as f64);
    Ok(())
}

fn measure_flips(
    settings: &Settings,
    clock: &Clock,
    log: &mut MachineLog,
    cpu: usize,
) -> anyhow::Result<FlipRows> {
    let mut rows = FlipRows::default();
    for repeat in 0..settings.read_repeats {
        for &n in &settings.flip_sizes {
            log.record(&format!("flips n={n} repeat={repeat}"), &[cpu]);
            for short in [false, true] {
                flip_block::<Array>(settings, clock, n, short, repeat, &mut rows)?;
                flip_block::<Blocks>(settings, clock, n, short, repeat, &mut rows)?;
            }
        }
    }
    Ok(rows)
}

// --------------------------------------------------- 2. cost per application

#[derive(Default)]
struct CostRows {
    n: Vec<u32>,
    layout: Vec<String>,
    state: Vec<String>,
    operator: Vec<String>,
    anchor: Vec<u32>,
    repeat: Vec<u32>,
    delta: Vec<i64>,
    time_ns: Vec<f64>,
    matrix_reads: Vec<i64>,
    cache_reads: Vec<i64>,
    positions: Vec<i64>,
    flips: Vec<i64>,
    suspect: Vec<bool>,
}

/// One row per (n, layout, state, operator, repeat): the batched passes.
#[derive(Default)]
struct CostBatchRows {
    n: Vec<u32>,
    layout: Vec<String>,
    state: Vec<String>,
    operator: Vec<String>,
    repeat: Vec<u32>,
    /// Round within the repeat (see [`Settings::rounds`]).
    round: Vec<u32>,
    applications: Vec<i64>,
    /// One region around every anchor, each restored once.
    batch_ns: Vec<f64>,
    /// Replays of every improving application's changes, each restored
    /// (about two restores each); absent when nothing improves.
    restore_batch_ns: Vec<Option<f64>>,
    /// Estimated restore time of the batch.
    restore_ns: Vec<f64>,
    /// Time per application, restore removed: the operator's time.
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
    /// Samples of this (operator, repeat) taken again because another task
    /// disturbed them (`timing::disturb`).
    retaken: Vec<i64>,
    suspect: Vec<bool>,
}

/// The calling thread's core-cycle counters and the reference frequency
/// measured with them on the timed core, for the per-block drift check.
struct Frequency {
    counters: Option<ThreadCounters>,
    reference_khz: Option<f64>,
}

impl Frequency {
    /// Opens the counters on the calling thread and measures its reference
    /// frequency; without a counter (only possible with
    /// `--allow-unlocked-clock`, where its absence is a recorded machine
    /// problem) the check falls back to the kernel's snapshots.
    fn on_this_thread() -> anyhow::Result<Self> {
        let mut counters = ThreadCounters::open()
            .inspect_err(|error| log::warn!("no core-cycle counter: {error:#}"))
            .ok();
        let reference_khz = match counters.as_mut() {
            Some(counters) => Some(counters.reference_khz(200)?),
            None => None,
        };
        Ok(Self {
            counters,
            reference_khz,
        })
    }

    fn read(&mut self) -> anyhow::Result<Option<Reading>> {
        self.counters.as_mut().map(ThreadCounters::read).transpose()
    }

    /// Whether the block since `before` ran more than the tolerance away
    /// from the reference frequency.
    fn drifted_since(&mut self, before: Option<Reading>) -> anyhow::Result<bool> {
        let average = match (self.read()?, before) {
            (Some(after), Some(before)) => after.khz_since(&before),
            _ => None,
        };
        Ok(cycles::drifted(
            self.reference_khz,
            average,
            FREQUENCY_DRIFT,
        ))
    }
}

/// Times every (operator, anchor) from `order` as measurement A does
/// (`ideas/rust-port.md` §7.4): operators in a shuffled order, each over
/// every anchor (shuffled) back to back, in the passes of [`Pass::order`]
/// (per call, batched, and a restore batch when some application
/// improves).  Per-call rows go to `rows`, one batched row per operator to
/// `batches`.  Every application must reproduce its change in `expected`.
#[allow(clippy::too_many_arguments)]
fn cost_block<L: Layout>(
    settings: &Settings,
    clock: &Clock,
    set: &StateSet,
    state: &str,
    order: &[City],
    anchors: &[City],
    expected: &HashMap<(Op, City), i64>,
    repeat: usize,
    cpu: usize,
    frequency: &mut Frequency,
    rows: &mut CostRows,
    batches: &mut CostBatchRows,
) -> anyhow::Result<()> {
    let n = set.n();
    let problem = set.problem();
    let mut tour: Tour<L> = Tour::new(order, &set.instance.matrix);
    crate::timing::disturb::spin_up(crate::timing::disturb::SPIN_UP_MS);
    let length = tour.length();
    // Operator and pass order: the same in every repeat (not drawn per
    // repeat), so that every repeat replays one identical sequence from the
    // same fresh tour.  The two-level list's internal state after an undo
    // depends on what ran before, so with an order drawn per repeat the
    // same application moved different numbers of positions in different
    // repeats (2026-11-02).
    let mut order_rng = Stream::new(
        settings.seed,
        "e0-cost-order",
        &[n as u64, label_key(L::NAME), label_key(state)],
    );
    let mut ops = Op::ALL.to_vec();
    order_rng.shuffle(&mut ops);
    let label = format!("e0:{n}:{state}");
    let before = khz(cpu);
    let reading = frequency.read()?;
    let (first_row, first_batch) = (rows.n.len(), batches.n.len());
    let mismatch = |op: Op, anchor: City, delta: i64| {
        anyhow::anyhow!(
            "{} at anchor {anchor} (n={n}, {state}, {}) changed the length by {delta}, the untimed pass by {}",
            op.name(),
            L::NAME,
            expected[&(op, anchor)]
        )
    };
    // Rounds over every operator, as measurement A times a state: a burst
    // of activity elsewhere on the chip (tens of milliseconds) slows every
    // sample of one operator taken back to back, so each repeat takes the
    // median over rounds that are spread over the whole block.  The order
    // of each round is drawn from `order_rng`: the same in every repeat.
    for round in 0..settings.rounds {
        let mut round_ops = ops.clone();
        if round > 0 {
            order_rng.shuffle(&mut round_ops);
        }
        for &op in &round_ops {
            crate::timing::disturb::take_retakes();
            // The same anchor order and streams in every repeat and layout:
            // repeats replay identical applications in the same order, so their
            // spread is the machine's.  (The order was drawn per repeat until
            // 2026-11-02; the order alone changed the batched time of cheap
            // 2-opt operators at n = 1000 by up to 2.5 times, through which
            // distance rows stay in cache.)
            let mut shuffled = anchors.to_vec();
            Stream::new(
                settings.seed,
                "e0-cost-anchor-order",
                &[n as u64, label_key(state), op as u64],
            )
            .shuffle(&mut shuffled);
            let stream =
                |anchor: City| Stream::new(settings.seed, &label, &[op as u64, u64::from(anchor)]);
            let improves = shuffled.iter().any(|&anchor| expected[&(op, anchor)] < 0);
            let (mut single, mut restores) = (None, None);
            warm_up(&mut tour, &problem, op, &shuffled, stream);
            for pass in Pass::order(improves, &mut order_rng) {
                // Per-call times are a diagnostic: the first round only.
                if pass == Pass::PerCall && round > 0 {
                    continue;
                }
                if pass == Pass::Restore {
                    let improving: Vec<City> = shuffled
                        .iter()
                        .copied()
                        .filter(|&anchor| expected[&(op, anchor)] < 0)
                        .collect();
                    let replays = improving_replays(&mut tour, &problem, op, &improving, stream);
                    if replays.len() != improving.len() {
                        bail!(
                            "{} improved at {} of {} anchors expected (n={n}, {state}, {})",
                            op.name(),
                            replays.len(),
                            improving.len(),
                            L::NAME
                        );
                    }
                    restores = Some(timed_restores(&mut tour, &replays, clock));
                } else if pass == Pass::PerCall {
                    for &anchor in &shuffled {
                        let mark = tour.mark();
                        let mut rng = stream(anchor);
                        let mut work = Work::default();
                        let begin = tsc::start();
                        let application =
                            op.apply(&mut tour, &problem, &mut rng, &mut work, Some(anchor));
                        let end = tsc::stop();
                        tour.undo_to(mark);
                        if application.delta != expected[&(op, anchor)] {
                            return Err(mismatch(op, anchor, application.delta));
                        }
                        rows.n.push(n as u32);
                        rows.layout.push(L::NAME.to_string());
                        rows.state.push(state.to_string());
                        rows.operator.push(op.name().to_string());
                        rows.anchor.push(anchor);
                        rows.repeat.push(repeat as u32);
                        rows.delta.push(application.delta);
                        rows.time_ns.push(clock.region_ns(begin, end));
                        rows.matrix_reads.push(work.matrix_reads as i64);
                        rows.cache_reads.push(work.cache_reads as i64);
                        rows.positions.push(work.positions as i64);
                        rows.flips.push(work.flips as i64);
                    }
                } else {
                    let mut deltas = vec![0i64; shuffled.len()];
                    let elapsed = sampled_batch(
                        &mut tour,
                        &problem,
                        op,
                        &shuffled,
                        stream,
                        clock,
                        &mut deltas,
                    )?;
                    if let Some((&anchor, &delta)) = shuffled
                        .iter()
                        .zip(&deltas)
                        .find(|&(&anchor, &delta)| delta != expected[&(op, anchor)])
                    {
                        return Err(mismatch(op, anchor, delta));
                    }
                    single = Some(elapsed);
                }
                if tour.length() != length {
                    bail!("{} did not restore the state (n={n}, {state})", op.name());
                }
            }
            let single = single.expect("the batch pass always runs");
            let (restore, per_application) =
                batched_time(single.ns, restores.map(|sample| sample.ns), shuffled.len());
            let counts = batched_counts(single, restores, shuffled.len());
            batches.n.push(n as u32);
            batches.layout.push(L::NAME.to_string());
            batches.state.push(state.to_string());
            batches.operator.push(op.name().to_string());
            batches.repeat.push(repeat as u32);
            batches.applications.push(shuffled.len() as i64);
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
            batches.round.push(round as u32);
            batches
                .retaken
                .push(crate::timing::disturb::take_retakes() as i64);
        }
    }
    let suspect = drifted(before, khz(cpu)) || frequency.drifted_since(reading)?;
    rows.suspect
        .extend(std::iter::repeat_n(suspect, rows.n.len() - first_row));
    batches
        .suspect
        .extend(std::iter::repeat_n(suspect, batches.n.len() - first_batch));
    tour.check(&set.instance.matrix)
        .map_err(|message| anyhow::anyhow!("n={n} {state}: {message}"))?;
    Ok(())
}

fn label_key(label: &str) -> u64 {
    crate::rng::label_hash(label)
}

/// The anchors of the cost sweep for one state (a fixed sample).
fn cost_anchors(settings: &Settings, n: usize, state: &str) -> Vec<City> {
    Stream::new(
        settings.seed,
        "e0-cost-anchors",
        &[n as u64, label_key(state)],
    )
    .sample(n, settings.anchors.min(n))
    .into_iter()
    .map(|city| city as City)
    .collect()
}

/// Length change of every (operator, anchor) application on one state.
type Changes = HashMap<(Op, City), i64>;

/// Every application's length change on one state, untimed: what each
/// timed pass must reproduce, and which operators need a restore
/// batch.  The same in every repeat and layout (same streams, and both
/// layouts give the same logical tour).
fn expected_changes(settings: &Settings, set: &StateSet, state: &str, order: &[City]) -> Changes {
    let n = set.n();
    let problem = set.problem();
    let mut tour: Tour<Array> = Tour::new(order, &set.instance.matrix);
    let label = format!("e0:{n}:{state}");
    let mut changes = HashMap::new();
    for op in Op::ALL {
        for anchor in cost_anchors(settings, n, state) {
            let mark = tour.mark();
            let mut rng = Stream::new(settings.seed, &label, &[op as u64, u64::from(anchor)]);
            let delta = op
                .apply(
                    &mut tour,
                    &problem,
                    &mut rng,
                    &mut Work::default(),
                    Some(anchor),
                )
                .delta;
            tour.undo_to(mark);
            changes.insert((op, anchor), delta);
        }
    }
    changes
}

fn measure_costs(
    settings: &Settings,
    clock: &Clock,
    sets: &BTreeMap<usize, StateSet>,
    log: &mut MachineLog,
    cpu: usize,
    frequency: &mut Frequency,
) -> anyhow::Result<(CostRows, CostBatchRows)> {
    let mut expected: HashMap<(usize, &str), Changes> = HashMap::new();
    for &n in &settings.cost_sizes {
        for (state, order) in &sets[&n].states {
            expected.insert(
                (n, *state),
                expected_changes(settings, &sets[&n], state, order),
            );
        }
    }
    let mut rows = CostRows::default();
    let mut batches = CostBatchRows::default();
    for repeat in 0..settings.repeats {
        for &n in &settings.cost_sizes {
            let set = &sets[&n];
            log.record(&format!("costs n={n} repeat={repeat}"), &[cpu]);
            let started = std::time::Instant::now();
            for (state, order) in &set.states {
                let anchors = cost_anchors(settings, n, state);
                let changes = &expected[&(n, *state)];
                cost_block::<Array>(
                    settings,
                    clock,
                    set,
                    state,
                    order,
                    &anchors,
                    changes,
                    repeat,
                    cpu,
                    frequency,
                    &mut rows,
                    &mut batches,
                )?;
                cost_block::<Blocks>(
                    settings,
                    clock,
                    set,
                    state,
                    order,
                    &anchors,
                    changes,
                    repeat,
                    cpu,
                    frequency,
                    &mut rows,
                    &mut batches,
                )?;
            }
            log::info!(
                "cost sweep repeat {}/{} n={n}: {:.1} s",
                repeat + 1,
                settings.repeats,
                started.elapsed().as_secs_f64()
            );
        }
    }
    Ok((rows, batches))
}

// ------------------------------------------------------------- 6. descents

#[derive(Default)]
struct DescentRows {
    n: Vec<u32>,
    layout: Vec<String>,
    repeat: Vec<u32>,
    time_ns: Vec<f64>,
    start_length: Vec<i64>,
    end_length: Vec<i64>,
    matrix_reads: Vec<i64>,
    cache_reads: Vec<i64>,
    positions: Vec<i64>,
    flips: Vec<i64>,
}

fn descent<L: Layout>(
    settings: &Settings,
    clock: &Clock,
    set: &StateSet,
    repeat: usize,
    rows: &mut DescentRows,
) -> anyhow::Result<()> {
    let n = set.n();
    let order = random_order(n, settings.seed + 1 + repeat as u64, "e0-descent");
    let mut tour: Tour<L> = Tour::new(&order, &set.instance.matrix);
    let start_length = tour.length();
    let all: Vec<City> = (0..n as City).collect();
    let mut work = Work::default();
    let problem = set.problem();
    let begin = tsc::start();
    descend(&mut tour, &problem, &mut work, &all, None);
    let end = tsc::stop();
    tour.check(&set.instance.matrix)
        .map_err(|message| anyhow::anyhow!("descent n={n}: {message}"))?;
    rows.n.push(n as u32);
    rows.layout.push(L::NAME.to_string());
    rows.repeat.push(repeat as u32);
    rows.time_ns.push(clock.region_ns(begin, end));
    rows.start_length.push(start_length);
    rows.end_length.push(tour.length());
    rows.matrix_reads.push(work.matrix_reads as i64);
    rows.cache_reads.push(work.cache_reads as i64);
    rows.positions.push(work.positions as i64);
    rows.flips.push(work.flips as i64);
    Ok(())
}

fn measure_descents(
    settings: &Settings,
    clock: &Clock,
    sets: &BTreeMap<usize, StateSet>,
    log: &mut MachineLog,
    cpu: usize,
) -> anyhow::Result<DescentRows> {
    let mut rows = DescentRows::default();
    for repeat in 0..settings.descent_repeats {
        for &n in &settings.cost_sizes {
            log.record(&format!("descents n={n} repeat={repeat}"), &[cpu]);
            descent::<Array>(settings, clock, &sets[&n], repeat, &mut rows)?;
            descent::<Blocks>(settings, clock, &sets[&n], repeat, &mut rows)?;
        }
    }
    Ok(rows)
}

// ----------------------------------------------------------- 3. contention

#[derive(Default)]
struct ContentionRows {
    phase: Vec<u32>,
    workers: Vec<u32>,
    worker: Vec<u32>,
    cpu: Vec<u32>,
    round: Vec<u32>,
    elapsed_s: Vec<f64>,
    n: Vec<u32>,
    state: Vec<String>,
    operator: Vec<String>,
    applications: Vec<i64>,
    total_ns: Vec<f64>,
    /// User-space instructions of the round's applications of the operator
    /// (absent where the performance counters cannot be read).
    total_instructions: Vec<Option<f64>>,
    /// User-space core cycles of the same.
    total_cycles: Vec<Option<f64>>,
    average_khz: Vec<Option<f64>>,
}

/// Per operator and round of a contention worker: the applications of its
/// batch, the batch's time and its counts.
struct Totals {
    applications: i64,
    ns: f64,
    counts: Option<Counts>,
}

impl ContentionRows {
    fn append(&mut self, other: Self) {
        self.phase.extend(other.phase);
        self.workers.extend(other.workers);
        self.worker.extend(other.worker);
        self.cpu.extend(other.cpu);
        self.round.extend(other.round);
        self.elapsed_s.extend(other.elapsed_s);
        self.n.extend(other.n);
        self.state.extend(other.state);
        self.operator.extend(other.operator);
        self.applications.extend(other.applications);
        self.total_ns.extend(other.total_ns);
        self.total_instructions.extend(other.total_instructions);
        self.total_cycles.extend(other.total_cycles);
        self.average_khz.extend(other.average_khz);
    }
}

/// One worker of one contention phase: identical rounds until `deadline`
/// (monotonic ns); a round still running at the deadline is discarded.
///
/// Each round times every operator once per (set, state), in an order drawn
/// per round (the same for every worker and phase), the way measurement A
/// times a state: an untimed warm-up pass over the anchors, then one sampled
/// batch ([`sampled_batch`]: the median of several samples, disturbed sets
/// taken again).  Until 2026-11-02 every application was timed on its own,
/// the operators interleaved call by call; the closing single-worker phase
/// then differed from the opening one by 2.7–4.2% while the cost sweep's
/// batched times agreed within 0.9% over the same interval.
#[allow(clippy::too_many_arguments)]
fn contention_worker(
    settings: &Settings,
    clock: &Clock,
    sets: &[&StateSet],
    phase: usize,
    workers: usize,
    worker: usize,
    cpu: usize,
    barrier: &Barrier,
    seconds: f64,
) -> anyhow::Result<ContentionRows> {
    pin::pin_current_thread(cpu)?;
    // Per (set, state): a tour and a fixed anchor sample, identical for
    // every worker and round.
    let mut blocks = Vec::new();
    for set in sets {
        for (state, order) in &set.states {
            let n = set.n();
            let tour: Tour<Array> = Tour::new(order, &set.instance.matrix);
            let anchors: Vec<City> =
                Stream::new(settings.seed, "e0c-anchors", &[n as u64, label_key(state)])
                    .sample(n, settings.contention_anchors.min(n))
                    .into_iter()
                    .map(|anchor| anchor as City)
                    .collect();
            blocks.push((*set, *state, tour, anchors));
        }
    }
    let mut rows = ContentionRows::default();
    barrier.wait();
    crate::timing::disturb::spin_up(crate::timing::disturb::SPIN_UP_MS);
    let started = tsc::monotonic_raw_ns();
    let deadline = started + (seconds * 1e9) as u64;
    let mut round = 0u32;
    'rounds: loop {
        let mut round_rows: Vec<(usize, &str, Op, Totals)> = Vec::new();
        for (set, state, tour, anchors) in &mut blocks {
            let n = set.n();
            let problem = set.problem();
            let label = format!("e0c:{n}:{state}");
            let mut ops = Op::ALL.to_vec();
            Stream::new(
                settings.seed,
                "e0c-order",
                &[n as u64, label_key(state), u64::from(round)],
            )
            .shuffle(&mut ops);
            for op in ops {
                if tsc::monotonic_raw_ns() > deadline {
                    break 'rounds;
                }
                let stream = |anchor: City| {
                    Stream::new(settings.seed, &label, &[op as u64, u64::from(anchor)])
                };
                warm_up(tour, &problem, op, anchors, stream);
                let mut deltas = vec![0i64; anchors.len()];
                let sample =
                    sampled_batch(tour, &problem, op, anchors, stream, clock, &mut deltas)?;
                round_rows.push((
                    n,
                    *state,
                    op,
                    Totals {
                        applications: anchors.len() as i64,
                        ns: sample.ns,
                        counts: sample.counts,
                    },
                ));
            }
        }
        let elapsed = (tsc::monotonic_raw_ns() - started) as f64 / 1e9;
        let frequency = khz(cpu);
        for (n, state, op, totals) in round_rows {
            rows.phase.push(phase as u32);
            rows.workers.push(workers as u32);
            rows.worker.push(worker as u32);
            rows.cpu.push(cpu as u32);
            rows.round.push(round);
            rows.elapsed_s.push(elapsed);
            rows.n.push(n as u32);
            rows.state.push(state.to_string());
            rows.operator.push(op.name().to_string());
            rows.applications.push(totals.applications);
            rows.total_ns.push(totals.ns);
            rows.total_instructions
                .push(totals.counts.map(|counts| counts.instructions));
            rows.total_cycles
                .push(totals.counts.map(|counts| counts.cycles));
            rows.average_khz.push(frequency);
        }
        round += 1;
    }
    Ok(rows)
}

fn measure_contention(
    settings: &Settings,
    clock: &Clock,
    sets: &BTreeMap<usize, StateSet>,
    cores: &[usize],
    log: &mut MachineLog,
) -> anyhow::Result<ContentionRows> {
    let chosen: Vec<&StateSet> = settings.contention_sizes.iter().map(|n| &sets[n]).collect();
    let mut rows = ContentionRows::default();
    let opening_temperature = machine::temperature_c();
    let last = settings.contention_workers.len() - 1;
    for (phase, &workers) in settings.contention_workers.iter().enumerate() {
        if phase == last && phase > 0 {
            cool_down(opening_temperature);
        }
        let seconds = if phase == 0 || phase == last {
            settings.contention_single_seconds
        } else {
            settings.contention_seconds
        };
        log.record(
            &format!("contention phase {phase} start"),
            &cores[..workers],
        );
        log::info!(
            "contention phase {phase}: {workers} worker(s) for {seconds:.0} s ({:?} °C, fabric clock {:?} MHz)",
            machine::temperature_c(),
            machine::soc_clock_mhz("fclk"),
        );
        let barrier = Barrier::new(workers);
        let results: Vec<anyhow::Result<ContentionRows>> = std::thread::scope(|scope| {
            let handles: Vec<_> = (0..workers)
                .map(|worker| {
                    let chosen = &chosen;
                    let barrier = &barrier;
                    let cpu = cores[worker];
                    scope.spawn(move || {
                        contention_worker(
                            settings, clock, chosen, phase, workers, worker, cpu, barrier, seconds,
                        )
                    })
                })
                .collect();
            handles
                .into_iter()
                .map(|handle| {
                    handle
                        .join()
                        .unwrap_or_else(|_| Err(anyhow::anyhow!("contention worker panicked")))
                })
                .collect()
        });
        log.record(&format!("contention phase {phase} end"), &cores[..workers]);
        for result in results {
            let worker_rows = result?;
            if worker_rows.round.is_empty() {
                bail!(
                    "a worker of contention phase {phase} completed no round; lengthen the phase"
                );
            }
            rows.append(worker_rows);
        }
    }
    Ok(rows)
}

// ------------------------------------- 7. per-call against batched timing

/// The ways of timing the same applications that the per-call check
/// compares: one region per call with every operator interleaved at each
/// anchor (the old order of measurement A), one region per call grouped by
/// operator (the per-call pass of measurement A), and one region around
/// all of an operator's anchors (its batched pass).
const TIMING_MODES: [&str; 3] = ["mixed_per_call", "grouped_per_call", "batched"];

#[derive(Default)]
struct PerCallRows {
    n: Vec<u32>,
    layout: Vec<String>,
    operator: Vec<String>,
    repeat: Vec<u32>,
    mode: Vec<String>,
    applications: Vec<i64>,
    mean_ns: Vec<f64>,
}

/// Anchors of the O18-optimal state at which no deterministic operator
/// improves, so every application leaves the tour unchanged and all three
/// timing modes do exactly the same work (a batch then needs no restore).
fn unchanged_anchors(settings: &Settings, set: &StateSet) -> anyhow::Result<Vec<City>> {
    let n = set.n();
    let order = &set
        .states
        .iter()
        .find(|(name, _)| *name == "lkopt")
        .context("the state set has no O18 optimum")?
        .1;
    let problem = set.problem();
    let mut tour: Tour<Array> = Tour::new(order, &set.instance.matrix);
    let mut cities: Vec<City> = (0..n as City).collect();
    Stream::new(settings.seed, "e0-per-call-anchors", &[n as u64]).shuffle(&mut cities);
    let mut rng = Stream::new(settings.seed, "e0-per-call-filter", &[n as u64]);
    let mut anchors = Vec::with_capacity(settings.anchors);
    for city in cities {
        if anchors.len() >= settings.anchors {
            break;
        }
        let unchanged = Op::ALL
            .into_iter()
            .filter(|op| op.deterministic())
            .all(|op| {
                let mark = tour.mark();
                let delta = op
                    .apply(
                        &mut tour,
                        &problem,
                        &mut rng,
                        &mut Work::default(),
                        Some(city),
                    )
                    .delta;
                tour.undo_to(mark);
                delta == 0
            });
        if unchanged {
            anchors.push(city);
        }
    }
    if anchors.len() < settings.anchors.min(n) / 2 {
        bail!(
            "n={n}: only {} anchors leave every deterministic operator without a move",
            anchors.len()
        );
    }
    Ok(anchors)
}

/// One repeat of the per-call check on one layout: every deterministic
/// operator at every anchor, timed in each of [`TIMING_MODES`] (the modes
/// in a shuffled order).
fn per_call_block<L: Layout>(
    settings: &Settings,
    clock: &Clock,
    set: &StateSet,
    anchors: &[City],
    repeat: usize,
    rows: &mut PerCallRows,
) -> anyhow::Result<()> {
    let n = set.n();
    let problem = set.problem();
    let order = &set
        .states
        .iter()
        .find(|(name, _)| *name == "lkopt")
        .context("the state set has no O18 optimum")?
        .1;
    let mut tour: Tour<L> = Tour::new(order, &set.instance.matrix);
    crate::timing::disturb::spin_up(crate::timing::disturb::SPIN_UP_MS);
    let (length, hash) = (tour.length(), tour.hash());
    let mut ops: Vec<Op> = Op::ALL
        .into_iter()
        .filter(|op| op.deterministic())
        .collect();
    let mut anchors = anchors.to_vec();
    let label = format!("e0-per-call:{n}");
    let stream =
        |op: Op, anchor: City| Stream::new(settings.seed, &label, &[op as u64, u64::from(anchor)]);
    let mut order_rng = Stream::new(
        settings.seed,
        "e0-per-call-order",
        &[n as u64, label_key(L::NAME), repeat as u64],
    );
    let mut modes = TIMING_MODES.to_vec();
    order_rng.shuffle(&mut modes);
    // (total ns, applications) per (mode, operator).
    let mut totals: BTreeMap<(&str, Op), (f64, i64)> = BTreeMap::new();
    let mut moved = false;
    for mode in modes {
        match mode {
            "mixed_per_call" => {
                order_rng.shuffle(&mut anchors);
                for &anchor in &anchors {
                    order_rng.shuffle(&mut ops);
                    for &op in &ops {
                        let mut rng = stream(op, anchor);
                        let mut work = Work::default();
                        let begin = tsc::start();
                        let delta = op
                            .apply(&mut tour, &problem, &mut rng, &mut work, Some(anchor))
                            .delta;
                        let end = tsc::stop();
                        moved |= delta != 0;
                        let entry = totals.entry((mode, op)).or_default();
                        entry.0 += clock.region_ns(begin, end);
                        entry.1 += 1;
                    }
                }
            }
            "grouped_per_call" => {
                order_rng.shuffle(&mut ops);
                for &op in &ops {
                    order_rng.shuffle(&mut anchors);
                    for &anchor in &anchors {
                        let mut rng = stream(op, anchor);
                        let mut work = Work::default();
                        let begin = tsc::start();
                        let delta = op
                            .apply(&mut tour, &problem, &mut rng, &mut work, Some(anchor))
                            .delta;
                        let end = tsc::stop();
                        moved |= delta != 0;
                        let entry = totals.entry((mode, op)).or_default();
                        entry.0 += clock.region_ns(begin, end);
                        entry.1 += 1;
                    }
                }
            }
            _ => {
                order_rng.shuffle(&mut ops);
                for &op in &ops {
                    order_rng.shuffle(&mut anchors);
                    let mut streams: Vec<Stream> =
                        anchors.iter().map(|&anchor| stream(op, anchor)).collect();
                    let mut work = Work::default();
                    let mut changed = 0i64;
                    let begin = tsc::start();
                    for (rng, &anchor) in streams.iter_mut().zip(&anchors) {
                        changed |= op
                            .apply(&mut tour, &problem, rng, &mut work, Some(anchor))
                            .delta;
                    }
                    let end = tsc::stop();
                    moved |= changed != 0;
                    let entry = totals.entry((mode, op)).or_default();
                    entry.0 += clock.region_ns(begin, end);
                    entry.1 += anchors.len() as i64;
                }
            }
        }
    }
    if moved || tour.length() != length || tour.hash() != hash {
        bail!(
            "per-call check n={n} {}: an operator changed the O18-optimal tour",
            L::NAME
        );
    }
    for ((mode, op), (total, count)) in totals {
        rows.n.push(n as u32);
        rows.layout.push(L::NAME.to_string());
        rows.operator.push(op.name().to_string());
        rows.repeat.push(repeat as u32);
        rows.mode.push(mode.to_string());
        rows.applications.push(count);
        rows.mean_ns.push(total / count as f64);
    }
    Ok(())
}

fn measure_per_call(
    settings: &Settings,
    clock: &Clock,
    sets: &BTreeMap<usize, StateSet>,
    log: &mut MachineLog,
    cpu: usize,
) -> anyhow::Result<PerCallRows> {
    let anchors: BTreeMap<usize, Vec<City>> = settings
        .cost_sizes
        .iter()
        .map(|&n| Ok((n, unchanged_anchors(settings, &sets[&n])?)))
        .collect::<anyhow::Result<_>>()?;
    let mut rows = PerCallRows::default();
    for repeat in 0..settings.repeats {
        for &n in &settings.cost_sizes {
            log.record(&format!("per-call check n={n} repeat={repeat}"), &[cpu]);
            per_call_block::<Array>(settings, clock, &sets[&n], &anchors[&n], repeat, &mut rows)?;
            per_call_block::<Blocks>(settings, clock, &sets[&n], &anchors[&n], repeat, &mut rows)?;
        }
    }
    Ok(rows)
}

/// Per (n, layout, operator): the medians over repeats of each mode's mean
/// time and the two per-call modes over the batched one.
fn per_call_summary(rows: &PerCallRows) -> Value {
    let mut means: BTreeMap<(u32, &str, &str), BTreeMap<&str, Vec<f64>>> = BTreeMap::new();
    for index in 0..rows.n.len() {
        means
            .entry((
                rows.n[index],
                rows.layout[index].as_str(),
                rows.operator[index].as_str(),
            ))
            .or_default()
            .entry(rows.mode[index].as_str())
            .or_default()
            .push(rows.mean_ns[index]);
    }
    let mut cells = BTreeMap::new();
    let (mut grouped, mut mixed) = (Vec::new(), Vec::new());
    for ((n, layout, op), modes) in &means {
        let medians: BTreeMap<&str, f64> = modes
            .iter()
            .map(|(mode, values)| (*mode, median(values)))
            .collect();
        let (Some(&batched), Some(&per_call), Some(&interleaved)) = (
            medians.get("batched"),
            medians.get("grouped_per_call"),
            medians.get("mixed_per_call"),
        ) else {
            continue;
        };
        grouped.push(per_call / batched);
        mixed.push(interleaved / batched);
        cells.insert(
            format!("n={n} {layout} {op}"),
            json!({
                "batched_ns": batched,
                "grouped_per_call_over_batched": per_call / batched,
                "mixed_per_call_over_batched": interleaved / batched,
            }),
        );
    }
    json!({
        "cells": cells,
        "median_grouped_per_call_over_batched": (!grouped.is_empty()).then(|| median(&grouped)),
        "median_mixed_per_call_over_batched": (!mixed.is_empty()).then(|| median(&mixed)),
    })
}

// ------------------------------------------------------------- summaries

/// (n, layout, state, operator).
type CellKey<'a> = (u32, &'a str, &'a str, &'a str);

/// A gate's verdict on one cell from its 95% interval: wholly inside the
/// limit, wholly beyond it, or straddling it (too few samples to tell; the
/// gate fails until more samples resolve it).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Verdict {
    Pass,
    Unresolved,
    Fail,
}

impl Verdict {
    fn name(self) -> &'static str {
        match self {
            Self::Pass => "pass",
            Self::Unresolved => "unresolved",
            Self::Fail => "fail",
        }
    }

    /// A quantity that must not exceed `limit`.
    fn at_most(low: f64, high: f64, limit: f64) -> Self {
        if high <= limit {
            Self::Pass
        } else if low > limit {
            Self::Fail
        } else {
            Self::Unresolved
        }
    }

    /// A change that must lie within ±`limit`.
    fn within(low: f64, high: f64, limit: f64) -> Self {
        if low >= -limit && high <= limit {
            Self::Pass
        } else if low > limit || high < -limit {
            Self::Fail
        } else {
            Self::Unresolved
        }
    }
}

/// One cell of the repeatability gate.
struct RepeatCell<'a> {
    key: CellKey<'a>,
    repeats: usize,
    /// Mean over repeats of the batched time per application, ns.
    mean_ns: f64,
    /// Coefficient of variation between repeats.
    cv: f64,
    /// 95% chi-square interval of `cv` ([`cv_interval`]).
    low: f64,
    high: f64,
    verdict: Verdict,
}

impl RepeatCell<'_> {
    fn label(&self) -> String {
        let (n, layout, state, op) = self.key;
        format!("n={n} {layout} {state} {op}")
    }
}

/// Per cell, the coefficient of variation between repeats of `summary` of
/// each repeat's values (the mean of per-call times over anchors, the
/// median of batched values over rounds); sorted, largest first.
fn cell_cvs(
    values: BTreeMap<CellKey<'_>, BTreeMap<u32, Vec<f64>>>,
    summary: fn(&[f64]) -> f64,
) -> Vec<(String, f64)> {
    let mut cvs: Vec<(String, f64)> = values
        .into_iter()
        .map(|((n, layout, state, op), repeats)| {
            let means: Vec<f64> = repeats.values().map(|times| summary(times)).collect();
            (
                format!("n={n} {layout} {state} {op}"),
                coefficient_of_variation(&means),
            )
        })
        .collect();
    cvs.sort_by(|a, b| b.1.total_cmp(&a.1));
    cvs
}

/// Repeatability: per (n, layout, state, operator), the coefficient of
/// variation between repeats of the **batched** time per application, the
/// time measurement A's rates use (the gate: every cell's 95% interval at
/// most [`GATE_CELL_CV`]; the median and 95th percentile over cells are
/// reported).  The same for the mean of the
/// per-call times is a diagnostic: for operators of a few tens of
/// nanoseconds it is dominated by the clock's 10 ns step and by single
/// interrupts, which the batched time does not see.  The same again for
/// the batched instructions and cycles per application (diagnostics): the
/// instruction count of an identical batch should not vary at all, and
/// cycles varying as much as time means the spread is in the core's own
/// execution (stalls), not time the thread lost to other tasks.
fn repeatability<'a>(batches: &'a CostBatchRows, rows: &CostRows) -> (Value, Vec<RepeatCell<'a>>) {
    let counted = |values: &[Option<f64>]| -> Vec<f64> {
        let mut grouped: BTreeMap<CellKey<'_>, BTreeMap<u32, Vec<f64>>> = BTreeMap::new();
        for (index, value) in values.iter().enumerate() {
            if let Some(value) = *value {
                grouped
                    .entry((
                        batches.n[index],
                        batches.layout[index].as_str(),
                        batches.state[index].as_str(),
                        batches.operator[index].as_str(),
                    ))
                    .or_default()
                    .entry(batches.repeat[index])
                    .or_default()
                    .push(value);
            }
        }
        cell_cvs(grouped, median)
            .into_iter()
            .map(|(_, cv)| cv)
            .collect()
    };
    let instruction_cvs = counted(&batches.instructions);
    let cycle_cvs = counted(&batches.cycles);
    let summary = |cvs: &[f64]| {
        (!cvs.is_empty()).then(|| json!({"median_cv": median(cvs), "p95_cv": quantile(cvs, 0.95)}))
    };
    let mut batched: BTreeMap<CellKey<'_>, BTreeMap<u32, Vec<f64>>> = BTreeMap::new();
    for index in 0..batches.n.len() {
        batched
            .entry((
                batches.n[index],
                batches.layout[index].as_str(),
                batches.state[index].as_str(),
                batches.operator[index].as_str(),
            ))
            .or_default()
            .entry(batches.repeat[index])
            .or_default()
            .push(batches.time_ns[index]);
    }
    let mut per_call: BTreeMap<CellKey<'_>, BTreeMap<u32, Vec<f64>>> = BTreeMap::new();
    for index in 0..rows.n.len() {
        per_call
            .entry((
                rows.n[index],
                rows.layout[index].as_str(),
                rows.state[index].as_str(),
                rows.operator[index].as_str(),
            ))
            .or_default()
            .entry(rows.repeat[index])
            .or_default()
            .push(rows.time_ns[index]);
    }
    let mut cells: Vec<RepeatCell> = batched
        .into_iter()
        .map(|(key, repeats)| {
            let times: Vec<f64> = repeats.values().map(|rounds| median(rounds)).collect();
            let cv = coefficient_of_variation(&times);
            let interval = cv_interval(cv, times.len());
            RepeatCell {
                key,
                repeats: times.len(),
                mean_ns: mean(&times),
                cv,
                low: interval.map_or(0.0, |(low, _)| low),
                high: interval.map_or(f64::INFINITY, |(_, high)| high),
                verdict: interval.map_or(Verdict::Unresolved, |(low, high)| {
                    Verdict::at_most(low, high, GATE_CELL_CV)
                }),
            }
        })
        .collect();
    cells.sort_by(|a, b| b.cv.total_cmp(&a.cv));
    let per_call_values: Vec<f64> = cell_cvs(per_call, mean)
        .into_iter()
        .map(|(_, cv)| cv)
        .collect();
    let values: Vec<f64> = cells.iter().map(|cell| cell.cv).collect();
    let count = |verdict: Verdict| cells.iter().filter(|cell| cell.verdict == verdict).count();
    let suspect = batches.suspect.iter().filter(|&&flag| flag).count();
    (
        json!({
            "timing": "batched",
            "cell_cv_limit": GATE_CELL_CV,
            "cells": values.len(),
            "passed_cells": count(Verdict::Pass),
            "unresolved_cells": count(Verdict::Unresolved),
            "failed_cells": count(Verdict::Fail),
            "passed": !cells.is_empty() && count(Verdict::Pass) == cells.len(),
            "median_cv": median(&values),
            "p95_cv": quantile(&values, 0.95),
            "max_cv": values.first(),
            "diagnostic_per_call_median_cv": median(&per_call_values),
            "diagnostic_per_call_p95_cv": quantile(&per_call_values, 0.95),
            "diagnostic_instructions": summary(&instruction_cvs),
            "diagnostic_cycles": summary(&cycle_cvs),
            "suspect_batches": suspect,
        }),
        cells,
    )
}

/// Quantile over (worker, round) of a cell's value per application: low,
/// because disturbances (interrupts, bursts of memory traffic from outside
/// the run) only ever slow a round down; the 25th percentile ignores them
/// without resting on the single fastest round.
const CONTENTION_QUANTILE: f64 = 0.25;

/// Per phase and operator, the change against the first phase of a value
/// per application (`values[row]`, a row's total; `None` rows skipped).
///
/// Per cell (operator, n, state) and phase: the [`CONTENTION_QUANTILE`]
/// over (worker, round) of the value per application; the cell's change is
/// that over the first phase's, and the operator's change the median over
/// its cells.  A summary for the counted diagnostics; the gate judges every
/// cell on its own ([`contention_cells`]).
fn phase_changes(
    rows: &ContentionRows,
    values: impl Fn(usize) -> Option<f64>,
) -> BTreeMap<(u32, &str), f64> {
    let mut per_cell: BTreeMap<(&str, u32, &str, u32), Vec<f64>> = BTreeMap::new();
    for index in 0..rows.phase.len() {
        let Some(value) = values(index) else { continue };
        per_cell
            .entry((
                rows.operator[index].as_str(),
                rows.n[index],
                rows.state[index].as_str(),
                rows.phase[index],
            ))
            .or_default()
            .push(value / rows.applications[index] as f64);
    }
    let levels: BTreeMap<(&str, u32, &str, u32), f64> = per_cell
        .iter()
        .map(|(key, values)| (*key, quantile(values, CONTENTION_QUANTILE)))
        .collect();
    let mut per_operator: BTreeMap<(u32, &str), Vec<f64>> = BTreeMap::new();
    for (&(op, n, state, phase), &level) in &levels {
        let Some(&base) = levels.get(&(op, n, state, 0)) else {
            continue;
        };
        if base > 0.0 {
            per_operator
                .entry((phase, op))
                .or_default()
                .push(level / base - 1.0);
        }
    }
    per_operator
        .into_iter()
        .map(|(key, changes)| (key, median(&changes)))
        .collect()
}

/// Bootstrap resamples of each contention cell's change.
const CONTENTION_BOOTSTRAP: usize = 1000;

/// One cell (operator, n, state) of one contention phase against the
/// opening phase.
struct ContentionCell<'a> {
    phase: u32,
    workers: u32,
    operator: &'a str,
    n: u32,
    state: &'a str,
    /// The opening phase's [`CONTENTION_QUANTILE`] of time per application.
    base_ns: f64,
    /// This phase's.
    level_ns: f64,
    /// `level_ns / base_ns - 1`.
    change: f64,
    /// 95% bootstrap interval of `change` (both phases' (worker, round)
    /// values resampled independently).
    low: f64,
    high: f64,
    verdict: Verdict,
}

impl ContentionCell<'_> {
    fn label(&self) -> String {
        format!("n={} {} {}", self.n, self.state, self.operator)
    }

    fn to_json(&self) -> Value {
        json!({
            "phase": self.phase,
            "workers": self.workers,
            "cell": self.label(),
            "change": self.change,
            "low": self.low,
            "high": self.high,
            "verdict": self.verdict.name(),
        })
    }
}

/// Every (operator, n, state) cell of every phase after the first, with
/// its change of time per application against the first phase and a
/// verdict.  A phase with several workers passes a cell when the cell's
/// whole interval is at most [`GATE_SLOWDOWN`] (a speed-up is no harm:
/// its workers share the instance's distance rows in L3); the closing
/// single-worker phase when the interval lies within ±[`GATE_SLOWDOWN`]
/// (drift either way).  Each cell is judged on its own, so a stable
/// operator cannot carry an unstable one (until 2026-11-02 the gate took
/// the median over operators' cells, or pooled their time).
fn contention_cells<'a>(settings: &Settings, rows: &'a ContentionRows) -> Vec<ContentionCell<'a>> {
    let mut per_cell: BTreeMap<(&str, u32, &str, u32), Vec<f64>> = BTreeMap::new();
    for index in 0..rows.phase.len() {
        per_cell
            .entry((
                rows.operator[index].as_str(),
                rows.n[index],
                rows.state[index].as_str(),
                rows.phase[index],
            ))
            .or_default()
            .push(rows.total_ns[index] / rows.applications[index] as f64);
    }
    let last = settings.contention_workers.len() as u32 - 1;
    let mut cells = Vec::new();
    for (&(operator, n, state, phase), values) in &per_cell {
        if phase == 0 {
            continue;
        }
        let Some(base) = per_cell.get(&(operator, n, state, 0)) else {
            continue;
        };
        let base_ns = quantile(base, CONTENTION_QUANTILE);
        if base_ns <= 0.0 {
            continue;
        }
        let level_ns = quantile(values, CONTENTION_QUANTILE);
        let mut stream = Stream::new(
            settings.seed,
            "e0-contention-bootstrap",
            &[
                u64::from(n),
                label_key(state),
                label_key(operator),
                u64::from(phase),
            ],
        );
        let mut resampled = vec![0.0; values.len()];
        let mut resampled_base = vec![0.0; base.len()];
        let mut changes = Vec::with_capacity(CONTENTION_BOOTSTRAP);
        for _ in 0..CONTENTION_BOOTSTRAP {
            for value in &mut resampled {
                *value = values[stream.index(values.len())];
            }
            for value in &mut resampled_base {
                *value = base[stream.index(base.len())];
            }
            changes.push(
                quantile(&resampled, CONTENTION_QUANTILE)
                    / quantile(&resampled_base, CONTENTION_QUANTILE)
                    - 1.0,
            );
        }
        let (low, high) = (quantile(&changes, 0.025), quantile(&changes, 0.975));
        let verdict = if phase == last {
            Verdict::within(low, high, GATE_SLOWDOWN)
        } else {
            Verdict::at_most(low, high, GATE_SLOWDOWN)
        };
        cells.push(ContentionCell {
            phase,
            workers: settings.contention_workers[phase as usize] as u32,
            operator,
            n,
            state,
            base_ns,
            level_ns,
            change: level_ns / base_ns - 1.0,
            low,
            high,
            verdict,
        });
    }
    cells
}

/// Contention and drift gates from the per-cell verdicts
/// ([`contention_cells`]): a phase passes when every cell passes.
fn contention_gate(settings: &Settings, rows: &ContentionRows, cells: &[ContentionCell]) -> Value {
    let last = settings.contention_workers.len() as u32 - 1;
    let mut phases = Vec::new();
    let mut verdicts: Vec<(usize, bool)> = Vec::new();
    for (phase, &workers) in settings.contention_workers.iter().enumerate().skip(1) {
        let phase = phase as u32;
        let in_phase: Vec<&ContentionCell> =
            cells.iter().filter(|cell| cell.phase == phase).collect();
        let count = |verdict: Verdict| {
            in_phase
                .iter()
                .filter(|cell| cell.verdict == verdict)
                .count()
        };
        // The worst cell: the largest slowdown, or in the closing phase the
        // largest change either way.
        let severity = |cell: &&ContentionCell| {
            if phase == last {
                cell.change.abs()
            } else {
                cell.change
            }
        };
        let worst = in_phase
            .iter()
            .max_by(|a, b| severity(a).total_cmp(&severity(b)));
        let passed = !in_phase.is_empty() && count(Verdict::Pass) == in_phase.len();
        if phase != last {
            verdicts.push((workers, passed));
        }
        phases.push(json!({
            "phase": phase,
            "workers": workers,
            "cells": in_phase.len(),
            "passed_cells": count(Verdict::Pass),
            "unresolved_cells": count(Verdict::Unresolved),
            "failed_cells": count(Verdict::Fail),
            "worst_cell": worst.map(|cell| cell.label()),
            "worst_change": worst.map(|cell| cell.change),
            "median_change": (!in_phase.is_empty())
                .then(|| median(&in_phase.iter().map(|cell| cell.change).collect::<Vec<_>>())),
            "passed": passed,
        }));
    }
    // The recommended count: the largest one such that it and every
    // smaller tested count pass.
    verdicts.sort_unstable();
    let recommended = verdicts
        .iter()
        .take_while(|(_, passed)| *passed)
        .map(|(workers, _)| *workers)
        .last()
        .unwrap_or(1);
    // The closing single-worker phase measures drift of the machine itself.
    let closing: Vec<&ContentionCell> = cells.iter().filter(|cell| cell.phase == last).collect();
    let drift = closing
        .iter()
        .map(|cell| cell.change.abs())
        .fold(0.0f64, f64::max);
    let mut non_passing: Vec<&ContentionCell> = cells
        .iter()
        .filter(|cell| cell.verdict != Verdict::Pass)
        .collect();
    non_passing.sort_by(|a, b| {
        a.phase
            .cmp(&b.phase)
            .then(b.change.abs().total_cmp(&a.change.abs()))
    });
    json!({
        "quantile": CONTENTION_QUANTILE,
        "limit": GATE_SLOWDOWN,
        "phases": phases,
        "closing_phase_max_drift": drift,
        "drift_ok": !closing.is_empty() && closing.iter().all(|cell| cell.verdict == Verdict::Pass),
        "recommended_workers": recommended,
        "non_passing": non_passing.iter().map(|cell| cell.to_json()).collect::<Vec<_>>(),
        "diagnostic_instructions": counted_phases(settings, rows, &rows.total_instructions),
        "diagnostic_cycles": counted_phases(settings, rows, &rows.total_cycles),
    })
}

/// The contention gate's slowdowns again for a counted quantity per
/// application (instructions or cycles), as a diagnostic: per phase the
/// median and worst change against the first phase, and the closing
/// phase's largest change.  Instructions should not change at all; cycles
/// changing as time does means the core ran the same work faster or
/// slower, while time changing alone means the thread lost time to
/// interrupts or other tasks.  `None` when no round was counted.
fn counted_phases(
    settings: &Settings,
    rows: &ContentionRows,
    values: &[Option<f64>],
) -> Option<Value> {
    let changes = phase_changes(rows, |index| values[index]);
    if changes.is_empty() {
        return None;
    }
    let changes = |phase: u32| -> Vec<f64> {
        Op::ALL
            .iter()
            .filter_map(|op| changes.get(&(phase, op.name())).copied())
            .collect()
    };
    let last = settings.contention_workers.len() as u32 - 1;
    let phases: Vec<Value> = (0..=last)
        .map(|phase| {
            let changes = changes(phase);
            json!({
                "phase": phase,
                "median_change": median(&changes),
                "largest_change": changes.iter().copied().fold(f64::NEG_INFINITY, f64::max),
                "smallest_change": changes.iter().copied().fold(f64::INFINITY, f64::min),
            })
        })
        .collect();
    let drift = changes(last)
        .into_iter()
        .map(f64::abs)
        .fold(0.0f64, f64::max);
    Some(json!({"phases": phases, "closing_phase_max_drift": drift}))
}

/// Representation threshold: per n, the geometric mean over (state,
/// operator) of the two-level/array ratio of batched times; the proposed
/// `n_split` is the smallest n from which the two-level list is faster at
/// every larger measured n.
fn crossover(rows: &CostBatchRows, flips: &FlipRows) -> Value {
    let mut means: BTreeMap<(u32, &str, &str, &str), (f64, usize)> = BTreeMap::new();
    for index in 0..rows.n.len() {
        let entry = means
            .entry((
                rows.n[index],
                rows.state[index].as_str(),
                rows.operator[index].as_str(),
                rows.layout[index].as_str(),
            ))
            .or_default();
        entry.0 += rows.time_ns[index];
        entry.1 += 1;
    }
    let mut log_ratios: BTreeMap<u32, Vec<f64>> = BTreeMap::new();
    for (&(n, state, op, layout), &(total, count)) in &means {
        if layout != Array::NAME {
            continue;
        }
        if let Some(&(other, other_count)) = means.get(&(n, state, op, Blocks::NAME)) {
            let ratio = (other / other_count as f64) / (total / count as f64);
            if ratio.is_finite() && ratio > 0.0 {
                log_ratios.entry(n).or_default().push(ratio.ln());
            }
        }
    }
    let operator_ratio: BTreeMap<u32, f64> = log_ratios
        .into_iter()
        .map(|(n, values)| (n, mean(&values).exp()))
        .collect();
    let proposal = operator_ratio
        .iter()
        .rev()
        .take_while(|(_, ratio)| **ratio < 1.0)
        .last()
        .map(|(n, _)| *n);
    let mut flip_means: BTreeMap<(u32, &str, &str), Vec<f64>> = BTreeMap::new();
    for index in 0..flips.n.len() {
        flip_means
            .entry((
                flips.n[index],
                flips.mode[index].as_str(),
                flips.layout[index].as_str(),
            ))
            .or_default()
            .push(flips.ns_per_flip[index]);
    }
    let flip_ratio: BTreeMap<String, f64> = flip_means
        .iter()
        .filter(|((_, _, layout), _)| *layout == Array::NAME)
        .filter_map(|(&(n, mode, _), values)| {
            let other = flip_means.get(&(n, mode, Blocks::NAME))?;
            Some((format!("{mode} n={n}"), median(other) / median(values)))
        })
        .collect();
    json!({
        "operator_time_ratio_two_level_over_array": operator_ratio,
        "flip_time_ratio_two_level_over_array": flip_ratio,
        "proposed_n_split": proposal,
    })
}

/// Per n and "mode access": the median ns per read, raw and net of its
/// baseline (`net` entries).
fn read_summary(rows: &ReadRows) -> BTreeMap<u32, BTreeMap<String, f64>> {
    let mut groups: BTreeMap<u32, BTreeMap<(String, String), Vec<f64>>> = BTreeMap::new();
    for index in 0..rows.n.len() {
        groups
            .entry(rows.n[index])
            .or_default()
            .entry((rows.mode[index].clone(), rows.access[index].clone()))
            .or_default()
            .push(rows.ns_per_read[index]);
    }
    groups
        .into_iter()
        .map(|(n, modes)| {
            let raw: BTreeMap<(String, String), f64> = modes
                .into_iter()
                .map(|(key, values)| (key, median(&values)))
                .collect();
            let mut out = BTreeMap::new();
            for ((mode, access), value) in &raw {
                out.insert(format!("{mode} {access}"), *value);
                if let Some(base) = read_baseline(mode)
                    .and_then(|base| raw.get(&(base.to_string(), access.clone())))
                {
                    out.insert(format!("net {mode} {access}"), value - base);
                }
            }
            (n, out)
        })
        .collect()
}

/// Batched time per application (µs, mean over repeats) per (state,
/// operator, n) on the array layout, for the summary table.
fn cost_table(rows: &CostBatchRows) -> BTreeMap<(String, String), BTreeMap<u32, f64>> {
    let mut sums: BTreeMap<(String, String), BTreeMap<u32, (f64, usize)>> = BTreeMap::new();
    for index in 0..rows.n.len() {
        if rows.layout[index] != Array::NAME {
            continue;
        }
        let entry = sums
            .entry((rows.state[index].clone(), rows.operator[index].clone()))
            .or_default()
            .entry(rows.n[index])
            .or_default();
        entry.0 += rows.time_ns[index];
        entry.1 += 1;
    }
    sums.into_iter()
        .map(|(key, by_n)| {
            (
                key,
                by_n.into_iter()
                    .map(|(n, (total, count))| (n, total / count as f64 / 1000.0))
                    .collect(),
            )
        })
        .collect()
}

fn descent_summary(rows: &DescentRows) -> BTreeMap<String, f64> {
    let mut groups: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    for index in 0..rows.n.len() {
        groups
            .entry(format!("{} n={}", rows.layout[index], rows.n[index]))
            .or_default()
            .push(rows.time_ns[index] / 1e6);
    }
    groups
        .into_iter()
        .map(|(key, values)| (key, median(&values)))
        .collect()
}

/// The per-call check as a table: operators by n, array layout.
fn per_call_markdown(check: &Value) -> String {
    let Some(cells) = check["cells"].as_object() else {
        return "No per-call check in this run.\n".to_string();
    };
    let mut table: BTreeMap<String, BTreeMap<u64, String>> = BTreeMap::new();
    let mut sizes = std::collections::BTreeSet::new();
    for (key, cell) in cells {
        let parts: Vec<&str> = key.split(' ').collect();
        let [size, layout, op] = parts[..] else {
            continue;
        };
        let Some(n) = size.strip_prefix("n=").and_then(|n| n.parse::<u64>().ok()) else {
            continue;
        };
        if layout != Array::NAME {
            continue;
        }
        sizes.insert(n);
        table.entry(op.to_string()).or_default().insert(
            n,
            format!(
                "{:.3} / {:.3}",
                cell["grouped_per_call_over_batched"]
                    .as_f64()
                    .unwrap_or(f64::NAN),
                cell["mixed_per_call_over_batched"]
                    .as_f64()
                    .unwrap_or(f64::NAN)
            ),
        );
    }
    let mut text = format!(
        "| Operator | {} |\n|---|{}\n",
        sizes
            .iter()
            .map(|n| format!("n={n}"))
            .collect::<Vec<_>>()
            .join(" | "),
        "---|".repeat(sizes.len())
    );
    for (op, by_n) in table {
        let row: Vec<String> = sizes
            .iter()
            .map(|n| by_n.get(n).cloned().unwrap_or_else(|| "-".to_string()))
            .collect();
        text += &format!("| {op} | {} |\n", row.join(" | "));
    }
    text += &format!(
        "\nMedian over all cells: grouped / batched {}, interleaved / batched {}.\n",
        check["median_grouped_per_call_over_batched"], check["median_mixed_per_call_over_batched"]
    );
    text
}

/// A markdown list of non-passing cells (`cell`, `value_key` with its
/// `low`–`high` interval, `verdict`, and the phase when present).
fn cell_list(cells: &Value, value_key: &str) -> String {
    let Some(cells) = cells.as_array() else {
        return String::new();
    };
    if cells.is_empty() {
        return "- none\n".to_string();
    }
    let percent = |value: &Value| value.as_f64().unwrap_or(f64::NAN) * 100.0;
    cells
        .iter()
        .map(|cell| {
            let phase = cell
                .get("phase")
                .map(|phase| format!("phase {phase}: "))
                .unwrap_or_default();
            format!(
                "- {phase}{} — {:+.2}% [{:+.2}%, {:+.2}%] {}\n",
                cell["cell"].as_str().unwrap_or("-"),
                percent(&cell[value_key]),
                percent(&cell["low"]),
                percent(&cell["high"]),
                cell["verdict"].as_str().unwrap_or("-"),
            )
        })
        .collect()
}

/// The repeatability gate's cells that did not pass, largest variation
/// first.
fn repeat_non_passing(cells: &[RepeatCell]) -> Vec<Value> {
    cells
        .iter()
        .filter(|cell| cell.verdict != Verdict::Pass)
        .map(|cell| {
            json!({
                "cell": cell.label(),
                "cv": cell.cv,
                "low": cell.low,
                "high": cell.high,
                "verdict": cell.verdict.name(),
            })
        })
        .collect()
}

/// Recomputes the repeatability, contention and drift gates of a finished
/// run (an `e0` output directory) from its tables with the current gate
/// rules, and writes them to `<directory>/gates/`: `summary.json` and the
/// two cell tables.  The run's own files are left as they are, so runs
/// made under earlier rules can be judged by the current ones.
///
/// # Errors
///
/// If the run's configuration or a table cannot be read, or the results
/// cannot be written.
pub fn regate(directory: &Path) -> anyhow::Result<Value> {
    use crate::io::parquet::{column_bool, column_f64, column_i64, column_str, read};
    let config: Value = serde_json::from_str(
        &std::fs::read_to_string(directory.join("config.json")).context("reading config.json")?,
    )
    .context("parsing config.json")?;
    let stored = &config["settings"]["settings"];
    let mut settings = Settings::full();
    settings.seed = stored["seed"].as_u64().context("config.json has no seed")?;
    settings.contention_workers = stored["contention_workers"]
        .as_array()
        .context("config.json has no contention_workers")?
        .iter()
        .map(|workers| workers.as_u64().map(|workers| workers as usize))
        .collect::<Option<_>>()
        .context("contention_workers holds a non-integer")?;
    let u32s = |batch: &arrow::record_batch::RecordBatch, name: &str| -> anyhow::Result<Vec<u32>> {
        column_i64(batch, name)?
            .into_iter()
            .map(|value| u32::try_from(value).with_context(|| format!("{name} = {value}")))
            .collect()
    };
    // Missing counts are stored as nulls, read back as NaN.
    let optional = |values: Vec<f64>| -> Vec<Option<f64>> {
        values
            .into_iter()
            .map(|value| (!value.is_nan()).then_some(value))
            .collect()
    };

    let batch = read(&directory.join("cost_batches.parquet"))?;
    let cost_batches = CostBatchRows {
        n: u32s(&batch, "n")?,
        layout: column_str(&batch, "layout")?,
        state: column_str(&batch, "state")?,
        operator: column_str(&batch, "operator")?,
        repeat: u32s(&batch, "repeat")?,
        time_ns: column_f64(&batch, "time_ns")?,
        instructions: optional(column_f64(&batch, "instructions")?),
        cycles: optional(column_f64(&batch, "cycles")?),
        suspect: column_bool(&batch, "suspect")?,
        ..CostBatchRows::default()
    };
    let batch = read(&directory.join("costs.parquet"))?;
    let costs = CostRows {
        n: u32s(&batch, "n")?,
        layout: column_str(&batch, "layout")?,
        state: column_str(&batch, "state")?,
        operator: column_str(&batch, "operator")?,
        repeat: u32s(&batch, "repeat")?,
        time_ns: column_f64(&batch, "time_ns")?,
        ..CostRows::default()
    };
    let batch = read(&directory.join("contention.parquet"))?;
    let contention = ContentionRows {
        phase: u32s(&batch, "phase")?,
        worker: u32s(&batch, "worker")?,
        round: u32s(&batch, "round")?,
        n: u32s(&batch, "n")?,
        state: column_str(&batch, "state")?,
        operator: column_str(&batch, "operator")?,
        applications: column_i64(&batch, "applications")?,
        total_ns: column_f64(&batch, "total_ns")?,
        total_instructions: optional(column_f64(&batch, "total_instructions")?),
        total_cycles: optional(column_f64(&batch, "total_cycles")?),
        ..ContentionRows::default()
    };

    let (repeat_gate, repeat_cells) = repeatability(&cost_batches, &costs);
    let contention_cells = contention_cells(&settings, &contention);
    let summary = json!({
        "source": directory,
        "repeatability": repeat_gate,
        "worst_cells": repeat_non_passing(&repeat_cells),
        "contention": contention_gate(&settings, &contention, &contention_cells),
    });
    let out = directory.join("gates");
    std::fs::create_dir_all(&out).with_context(|| format!("creating {}", out.display()))?;
    write(
        &out.join("repeat_cells.parquet"),
        repeat_cells_table(&repeat_cells),
    )?;
    write(
        &out.join("contention_cells.parquet"),
        contention_cells_table(&contention_cells),
    )?;
    write_atomic(
        &out.join("summary.json"),
        (serde_json::to_string_pretty(&summary)? + "\n").as_bytes(),
    )?;
    Ok(summary)
}

/// The repeatability gate's cells, one row each.
fn repeat_cells_table(cells: &[RepeatCell]) -> Table {
    Table::new()
        .with(
            "n",
            Column::U32(cells.iter().map(|cell| cell.key.0).collect()),
        )
        .with(
            "layout",
            Column::Str(cells.iter().map(|cell| cell.key.1.to_string()).collect()),
        )
        .with(
            "state",
            Column::Str(cells.iter().map(|cell| cell.key.2.to_string()).collect()),
        )
        .with(
            "operator",
            Column::Str(cells.iter().map(|cell| cell.key.3.to_string()).collect()),
        )
        .with(
            "repeats",
            Column::U32(cells.iter().map(|cell| cell.repeats as u32).collect()),
        )
        .with(
            "mean_ns",
            Column::F64(cells.iter().map(|cell| cell.mean_ns).collect()),
        )
        .with(
            "cv",
            Column::F64(cells.iter().map(|cell| cell.cv).collect()),
        )
        .with(
            "cv_low",
            Column::F64(cells.iter().map(|cell| cell.low).collect()),
        )
        .with(
            "cv_high",
            Column::F64(cells.iter().map(|cell| cell.high).collect()),
        )
        .with(
            "verdict",
            Column::Str(
                cells
                    .iter()
                    .map(|cell| cell.verdict.name().to_string())
                    .collect(),
            ),
        )
}

/// The contention gate's cells, one row per (phase, operator, n, state).
fn contention_cells_table(cells: &[ContentionCell]) -> Table {
    Table::new()
        .with(
            "phase",
            Column::U32(cells.iter().map(|cell| cell.phase).collect()),
        )
        .with(
            "workers",
            Column::U32(cells.iter().map(|cell| cell.workers).collect()),
        )
        .with("n", Column::U32(cells.iter().map(|cell| cell.n).collect()))
        .with(
            "state",
            Column::Str(cells.iter().map(|cell| cell.state.to_string()).collect()),
        )
        .with(
            "operator",
            Column::Str(cells.iter().map(|cell| cell.operator.to_string()).collect()),
        )
        .with(
            "base_ns",
            Column::F64(cells.iter().map(|cell| cell.base_ns).collect()),
        )
        .with(
            "level_ns",
            Column::F64(cells.iter().map(|cell| cell.level_ns).collect()),
        )
        .with(
            "change",
            Column::F64(cells.iter().map(|cell| cell.change).collect()),
        )
        .with(
            "change_low",
            Column::F64(cells.iter().map(|cell| cell.low).collect()),
        )
        .with(
            "change_high",
            Column::F64(cells.iter().map(|cell| cell.high).collect()),
        )
        .with(
            "verdict",
            Column::Str(
                cells
                    .iter()
                    .map(|cell| cell.verdict.name().to_string())
                    .collect(),
            ),
        )
}

fn markdown(summary: &Value, costs: &BTreeMap<(String, String), BTreeMap<u32, f64>>) -> String {
    let mut text = String::from("# E0-rs summary\n\n");
    text += &format!(
        "Machine problems at start: {}\n\n",
        summary["machine_problems"]
    );
    text += "## Clock\n\n```json\n";
    text += &serde_json::to_string_pretty(&summary["clock"]).unwrap_or_default();
    text += "\n```\n\n## Repeatability gate\n\n```json\n";
    text += &serde_json::to_string_pretty(&summary["repeatability"]).unwrap_or_default();
    text += "\n```\n\nCells that did not pass (coefficient of variation between repeats of the batched time per application, 95% interval; all in `repeat_cells.parquet`):\n\n";
    text += &cell_list(&summary["worst_cells"], "cv");
    text += &format!(
        "\nReference core frequency of the timed core (core-cycle counter): {} kHz.\n",
        summary["reference_core_khz"]
    );
    text += "\n## Per-call against batched timing (array layout, O18 optimum)\n\n";
    text += "Mean time per application timed one call at a time, over the same mean timed as one batch: grouped by operator (measurement A's per-call pass) / interleaved with every operator at each anchor (the old order).  Medians over repeats.\n\n";
    text += &per_call_markdown(&summary["per_call_check"]);
    text += "\n## Contention gate\n\nPer cell (operator, n, state): the 25th percentile over (worker, round) of time per application against the opening phase, with a 95% bootstrap interval; a phase passes when every cell does (all in `contention_cells.parquet`).\n\n| Phase | Workers | Passed | Unresolved | Failed | Worst cell | Worst change | Phase passed |\n|---|---|---|---|---|---|---|---|\n";
    if let Some(phases) = summary["contention"]["phases"].as_array() {
        for phase in phases {
            text += &format!(
                "| {} | {} | {} | {} | {} | {} | {:+.2}% | {} |\n",
                phase["phase"],
                phase["workers"],
                phase["passed_cells"],
                phase["unresolved_cells"],
                phase["failed_cells"],
                phase["worst_cell"].as_str().unwrap_or("-"),
                phase["worst_change"].as_f64().unwrap_or(f64::NAN) * 100.0,
                phase["passed"],
            );
        }
    }
    text += &format!(
        "\nClosing single-worker phase, largest drift: {:.2}% (drift gate passed: {}). Recommended timed workers: {}.\n\nCells that did not pass:\n\n",
        summary["contention"]["closing_phase_max_drift"]
            .as_f64()
            .unwrap_or(f64::NAN)
            * 100.0,
        summary["contention"]["drift_ok"],
        summary["contention"]["recommended_workers"]
    );
    text += &cell_list(&summary["contention"]["non_passing"], "change");
    text += "\n## Distance-read cost (median ns per read; net = minus its baseline)\n\n";
    if let Some(reads) = summary["reads"].as_object() {
        let mut sizes: Vec<(u64, &Value)> = reads
            .iter()
            .filter_map(|(n, values)| Some((n.parse().ok()?, values)))
            .collect();
        sizes.sort_by_key(|(n, _)| *n);
        let keys: Vec<String> = sizes
            .first()
            .and_then(|(_, values)| values.as_object())
            .map(|values| values.keys().cloned().collect())
            .unwrap_or_default();
        text += &format!(
            "| Read | {} |\n|---|{}\n",
            sizes
                .iter()
                .map(|(n, _)| format!("n={n}"))
                .collect::<Vec<_>>()
                .join(" | "),
            "---|".repeat(sizes.len())
        );
        for key in &keys {
            let cells: Vec<String> = sizes
                .iter()
                .map(|(_, values)| format!("{:.2}", values[key].as_f64().unwrap_or(f64::NAN)))
                .collect();
            text += &format!("| {key} | {} |\n", cells.join(" | "));
        }
    }
    text += "\n## Representation crossover\n\n```json\n";
    text += &serde_json::to_string_pretty(&summary["crossover"]).unwrap_or_default();
    text += "\n```\n\n## Full O7 descents (median ms)\n\n```json\n";
    text += &serde_json::to_string_pretty(&summary["descents_ms"]).unwrap_or_default();
    text += "\n```\n\n## Batched time per application, array layout (µs, mean over repeats)\n\n";
    let sizes: Vec<u32> = costs
        .values()
        .flat_map(|by_n| by_n.keys().copied())
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect();
    text += &format!(
        "| State | Operator | {} |\n|---|---|{}\n",
        sizes
            .iter()
            .map(|n| format!("n={n}"))
            .collect::<Vec<_>>()
            .join(" | "),
        "---|".repeat(sizes.len())
    );
    for ((state, op), by_n) in costs {
        let cells: Vec<String> = sizes
            .iter()
            .map(|n| {
                by_n.get(n)
                    .map_or("-".to_string(), |value| format!("{value:.3}"))
            })
            .collect();
        text += &format!("| {state} | {op} | {} |\n", cells.join(" | "));
    }
    text
}

// ------------------------------------------------------------------- run

/// Runs E0-rs and returns its run directory.
///
/// # Errors
///
/// If the machine is not locked for timing (unless `allow_unlocked`), too
/// few physical cores exist for the contention phases, an operator breaks
/// its contract, or a file cannot be written.
pub fn run(
    repository: &Path,
    settings: &Settings,
    allow_unlocked: bool,
    out: Option<PathBuf>,
) -> anyhow::Result<PathBuf> {
    let cores = pin::physical_cores()?;
    let max_workers = settings
        .contention_workers
        .iter()
        .copied()
        .max()
        .unwrap_or(1);
    if max_workers + 1 > cores.len() {
        bail!(
            "{max_workers} contention workers need {} physical cores (one for the main thread), found {}",
            max_workers + 1,
            cores.len()
        );
    }
    let main_core = *cores.last().expect("at least two cores");
    let timed_core = cores[0];
    let problems = machine::require_locked(&cores, allow_unlocked)?;
    let directory = match out {
        Some(directory) => {
            std::fs::create_dir_all(&directory)?;
            directory
        }
        None => new_run_directory(&repository.join("runs"), "e0", !problems.is_empty())?,
    };
    log::info!("E0-rs run directory: {}", directory.display());

    // Untimed: instances, alpha candidates and states, on every CPU.
    let sizes: std::collections::BTreeSet<usize> = settings
        .cost_sizes
        .iter()
        .chain(&settings.contention_sizes)
        .copied()
        .collect();
    let started = std::time::Instant::now();
    let sets: BTreeMap<usize, StateSet> = std::thread::scope(|scope| {
        let handles: Vec<_> = sizes
            .iter()
            .map(|&n| scope.spawn(move || (n, build_states(settings, n))))
            .collect();
        handles
            .into_iter()
            .map(|handle| {
                let (n, result) = handle
                    .join()
                    .map_err(|_| anyhow::anyhow!("state builder panicked"))?;
                Ok((n, result?))
            })
            .collect::<anyhow::Result<_>>()
    })?;
    log::info!(
        "instances and states ready in {:.1} s",
        started.elapsed().as_secs_f64()
    );

    pin::pin_current_thread(main_core)?;
    let mut log = MachineLog::default();
    log.record("start", &cores);
    let clock_start = on_core(timed_core, || read_clock("start"))?;
    let clock = clock_start.clock;
    write_config(
        &directory,
        repository,
        json!({
            "experiment": "E0-rs",
            "settings": settings,
            "cores": cores,
            "timed_core": timed_core,
            "main_core": main_core,
            "machine_problems": problems,
            "allow_unlocked_clock": allow_unlocked,
            "clock": clock,
            "instances": sets.values().map(|set| json!({
                "name": set.instance.name,
                "n": set.n(),
                "sha256": set.instance.sha256,
                "states": set.states.iter().map(|(name, order)| json!({
                    "name": name,
                    "length": crate::instance::tour_length(&set.instance.matrix, order),
                })).collect::<Vec<_>>(),
            })).collect::<Vec<_>>(),
            "representations": [Array::NAME, Blocks::NAME],
            "storage": "matrix",
            "candidate_cache": true,
        }),
    )?;

    log::info!("distance-read cost");
    let (reads, flips, costs, cost_batches, per_call, descents, reference_khz, log) =
        on_core(timed_core, || {
            let mut log = log;
            let mut frequency = Frequency::on_this_thread()?;
            let reads = measure_reads(settings, &clock, &mut log, timed_core)?;
            log::info!("flip cost");
            let flips = measure_flips(settings, &clock, &mut log, timed_core)?;
            log::info!("cost per application");
            let (costs, cost_batches) = measure_costs(
                settings,
                &clock,
                &sets,
                &mut log,
                timed_core,
                &mut frequency,
            )?;
            log::info!("per-call against batched timing");
            let per_call = measure_per_call(settings, &clock, &sets, &mut log, timed_core)?;
            log::info!("full O7 descents");
            let descents = measure_descents(settings, &clock, &sets, &mut log, timed_core)?;
            Ok((
                reads,
                flips,
                costs,
                cost_batches,
                per_call,
                descents,
                frequency.reference_khz,
                log,
            ))
        })?;
    let mut log = log;
    let contention = measure_contention(settings, &clock, &sets, &cores[..max_workers], &mut log)?;
    let clock_end = on_core(timed_core, || read_clock("end"))?;
    log.record("end", &cores);

    let (repeat_gate, repeat_cells) = repeatability(&cost_batches, &costs);
    let contention_cells = contention_cells(settings, &contention);
    let summary = json!({
        "machine_problems": problems,
        "clock": {
            "start": clock_start,
            "end": clock_end,
            "tsc_drift": clock_end.clock.ticks_per_ns / clock_start.clock.ticks_per_ns - 1.0,
        },
        "reference_core_khz": reference_khz,
        "repeatability": repeat_gate,
        "per_call_check": per_call_summary(&per_call),
        "worst_cells": repeat_non_passing(&repeat_cells),
        "contention": contention_gate(settings, &contention, &contention_cells),
        "reads": read_summary(&reads),
        "crossover": crossover(&cost_batches, &flips),
        "descents_ms": descent_summary(&descents),
    });
    let table = cost_table(&cost_batches);
    write(
        &directory.join("repeat_cells.parquet"),
        repeat_cells_table(&repeat_cells),
    )?;
    write(
        &directory.join("contention_cells.parquet"),
        contention_cells_table(&contention_cells),
    )?;
    drop((repeat_cells, contention_cells));

    write(
        &directory.join("reads.parquet"),
        Table::new()
            .with("n", Column::U32(reads.n))
            .with("mode", Column::Str(reads.mode))
            .with("access", Column::Str(reads.access))
            .with("repeat", Column::U32(reads.repeat))
            .with("reads", Column::I64(reads.reads))
            .with("ns_per_read", Column::F64(reads.ns_per_read)),
    )?;
    write(
        &directory.join("flips.parquet"),
        Table::new()
            .with("n", Column::U32(flips.n))
            .with("layout", Column::Str(flips.layout))
            .with("mode", Column::Str(flips.mode))
            .with("repeat", Column::U32(flips.repeat))
            .with("flips", Column::I64(flips.flips))
            .with("ns_per_flip", Column::F64(flips.ns_per_flip))
            .with("positions_per_flip", Column::F64(flips.positions_per_flip)),
    )?;
    write(
        &directory.join("costs.parquet"),
        Table::new()
            .with("n", Column::U32(costs.n))
            .with("layout", Column::Str(costs.layout))
            .with("state", Column::Str(costs.state))
            .with("operator", Column::Str(costs.operator))
            .with("anchor", Column::U32(costs.anchor))
            .with("repeat", Column::U32(costs.repeat))
            .with("delta", Column::I64(costs.delta))
            .with("time_ns", Column::F64(costs.time_ns))
            .with("matrix_reads", Column::I64(costs.matrix_reads))
            .with("cache_reads", Column::I64(costs.cache_reads))
            .with("positions", Column::I64(costs.positions))
            .with("flips", Column::I64(costs.flips))
            .with("suspect", Column::Bool(costs.suspect)),
    )?;
    write(
        &directory.join("cost_batches.parquet"),
        Table::new()
            .with("n", Column::U32(cost_batches.n))
            .with("layout", Column::Str(cost_batches.layout))
            .with("state", Column::Str(cost_batches.state))
            .with("operator", Column::Str(cost_batches.operator))
            .with("repeat", Column::U32(cost_batches.repeat))
            .with("round", Column::U32(cost_batches.round))
            .with("applications", Column::I64(cost_batches.applications))
            .with("batch_ns", Column::F64(cost_batches.batch_ns))
            .with(
                "restore_batch_ns",
                Column::OptF64(cost_batches.restore_batch_ns),
            )
            .with("restore_ns", Column::F64(cost_batches.restore_ns))
            .with("time_ns", Column::F64(cost_batches.time_ns))
            .with(
                "batch_instructions",
                Column::OptF64(cost_batches.batch_instructions),
            )
            .with("batch_cycles", Column::OptF64(cost_batches.batch_cycles))
            .with("instructions", Column::OptF64(cost_batches.instructions))
            .with("cycles", Column::OptF64(cost_batches.cycles))
            .with("retaken", Column::I64(cost_batches.retaken))
            .with("suspect", Column::Bool(cost_batches.suspect)),
    )?;
    write(
        &directory.join("per_call.parquet"),
        Table::new()
            .with("n", Column::U32(per_call.n))
            .with("layout", Column::Str(per_call.layout))
            .with("operator", Column::Str(per_call.operator))
            .with("repeat", Column::U32(per_call.repeat))
            .with("mode", Column::Str(per_call.mode))
            .with("applications", Column::I64(per_call.applications))
            .with("mean_ns", Column::F64(per_call.mean_ns)),
    )?;
    write(
        &directory.join("descents.parquet"),
        Table::new()
            .with("n", Column::U32(descents.n))
            .with("layout", Column::Str(descents.layout))
            .with("repeat", Column::U32(descents.repeat))
            .with("time_ns", Column::F64(descents.time_ns))
            .with("start_length", Column::I64(descents.start_length))
            .with("end_length", Column::I64(descents.end_length))
            .with("matrix_reads", Column::I64(descents.matrix_reads))
            .with("cache_reads", Column::I64(descents.cache_reads))
            .with("positions", Column::I64(descents.positions))
            .with("flips", Column::I64(descents.flips)),
    )?;
    write(
        &directory.join("contention.parquet"),
        Table::new()
            .with("phase", Column::U32(contention.phase))
            .with("workers", Column::U32(contention.workers))
            .with("worker", Column::U32(contention.worker))
            .with("cpu", Column::U32(contention.cpu))
            .with("round", Column::U32(contention.round))
            .with("elapsed_s", Column::F64(contention.elapsed_s))
            .with("n", Column::U32(contention.n))
            .with("state", Column::Str(contention.state))
            .with("operator", Column::Str(contention.operator))
            .with("applications", Column::I64(contention.applications))
            .with("total_ns", Column::F64(contention.total_ns))
            .with(
                "total_instructions",
                Column::OptF64(contention.total_instructions),
            )
            .with("total_cycles", Column::OptF64(contention.total_cycles))
            .with("average_khz", Column::OptF64(contention.average_khz)),
    )?;
    write(&directory.join("machine.parquet"), log.into_table())?;
    write_atomic(
        &directory.join("summary.json"),
        (serde_json::to_string_pretty(&summary)? + "\n").as_bytes(),
    )?;
    write_atomic(
        &directory.join("summary.md"),
        markdown(&summary, &table).as_bytes(),
    )?;
    log::info!("E0-rs finished: {}", directory.display());
    Ok(directory)
}
