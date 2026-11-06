//! The E1 state builder (`aos/e1_states.py`, `ideas/rust-port.md` §8.2):
//! tours by local-optimality level along one search path per (instance,
//! seed).
//!
//! `random` and `nearest_neighbour` tours; `d25`–`d90`, the nearest-
//! neighbour tour after an O7 descent stopped at 25–90% of the full
//! descent's gain; `opt2`, the full descent; `oropt`, `opt3` and `lkopt`,
//! variable neighbourhood descents adding O9, O10 and the LK step (O18);
//! `ils1` and `ils2`, `lkopt` after n and then 4n further O8 kick trials,
//! each followed by the LK descent.  Descents visit cities in a seeded
//! random order.  Identical tours are kept but marked as duplicates.
//!
//! Output: `states/<instance>_s<seed>.json` in the format of the Python
//! builder, so measurement A reads either.  The random draws differ from
//! Python's (seeded xoshiro256++ streams, [`crate::rng`]), so the tours do
//! too; to measure the Python tours exactly, point measurement A at the
//! Python states.

use std::path::{Path, PathBuf};

use serde::Serialize;
use serde_json::json;

use super::common::{StateRecord, StatesFile, load_instance, parallel_map, untimed_workers};
use crate::City;
use crate::instance::Instance;
use crate::instance::storage::{Dist, Matrix};
use crate::io::provenance::write_config;
use crate::io::rundir::{new_run_directory, write_atomic};
use crate::ops::{Op, Problem, Work, descend, descend_until};
use crate::rng::{Stream, label_hash};
use crate::tour::Tour;
use crate::tour::array::Array;

/// The partial descents: state name and the share of the full descent's
/// gain at which the descent stops.
const PARTIAL_DESCENTS: [(&str, f64); 4] =
    [("d25", 0.25), ("d50", 0.5), ("d75", 0.75), ("d90", 0.9)];

/// The rounds of iterated local search: state name and kick trials, in
/// multiples of n.
const ILS_ROUNDS: [(&str, usize); 2] = [("ils1", 1), ("ils2", 4)];

/// The state names in building order ([`build`] checks that it builds
/// exactly these).
pub const STATE_NAMES: [&str; 12] = [
    "random",
    "nearest_neighbour",
    PARTIAL_DESCENTS[0].0,
    PARTIAL_DESCENTS[1].0,
    PARTIAL_DESCENTS[2].0,
    PARTIAL_DESCENTS[3].0,
    "opt2",
    "oropt",
    "opt3",
    "lkopt",
    ILS_ROUNDS[0].0,
    ILS_ROUNDS[1].0,
];

/// States with many defects, which the budgeted runs skip on large
/// instances (kicks and descents there run over the whole tour).
pub const EARLY_STATES: [&str; 2] = [STATE_NAMES[0], STATE_NAMES[1]];

/// Largest instance on which budgeted runs measure the [`EARLY_STATES`].
pub const EARLY_STATES_MAX_N: usize = 1000;

/// Position of a state name in [`STATE_NAMES`] (unknown names last).
pub fn state_rank(name: &str) -> usize {
    STATE_NAMES
        .iter()
        .position(|state| *state == name)
        .unwrap_or(STATE_NAMES.len())
}

/// Settings of one states run.
#[derive(Debug, Clone, Serialize)]
pub struct Settings {
    /// Instance paths relative to the repository root.
    pub instances: Vec<String>,
    /// State seeds.
    pub seeds: Vec<u64>,
    /// Instances above this size get only the first seed.
    pub single_seed_above: usize,
    /// Untimed worker threads.
    pub workers: usize,
}

/// The nearest-neighbour tour from `start`; ties go to the smaller index.
fn nearest_neighbour_tour(matrix: &Matrix, start: City) -> Vec<City> {
    let n = matrix.n();
    let mut visited = vec![false; n];
    let mut order = Vec::with_capacity(n);
    let mut current = start;
    visited[start as usize] = true;
    order.push(start);
    for _ in 1..n {
        let row = matrix.row(current);
        let next = (0..n)
            .filter(|&city| !visited[city])
            .min_by_key(|&city| (row[city], city))
            .expect("an unvisited city remains") as City;
        visited[next as usize] = true;
        order.push(next);
        current = next;
    }
    order
}

/// Applies O7 and `ops` at every city of `visit` until none improves at
/// any city (a variable neighbourhood descent).
fn vnd(tour: &mut Tour<Array>, problem: &Problem<'_, Matrix, true>, visit: &[City], ops: &[Op]) {
    let mut rng = Stream::new(0, "e1-states-vnd", &[]);
    let mut work = Work::default();
    loop {
        let mut improved = false;
        for &city in visit {
            for op in std::iter::once(&Op::TwoOptNl).chain(ops) {
                if op
                    .apply(tour, problem, &mut rng, &mut work, Some(city))
                    .delta
                    < 0
                {
                    improved = true;
                }
            }
            tour.clear_log();
        }
        if !improved {
            break;
        }
    }
}

/// Builds the states of one instance and seed.
///
/// # Errors
///
/// If a built tour fails its check.
pub fn build(instance: &Instance, path: &str, seed: u64) -> anyhow::Result<StatesFile> {
    let n = instance.n();
    let problem: Problem<'_, Matrix, true> = Problem::new(&instance.matrix, instance);
    let mut rng = Stream::new(seed, "e1-states", &[label_hash(&instance.name)]);
    let mut random: Vec<City> = (0..n as City).collect();
    rng.shuffle(&mut random);
    let mut visit: Vec<City> = (0..n as City).collect();
    rng.shuffle(&mut visit);
    let mut states: Vec<StateRecord> = Vec::new();
    let mut add = |name: String, tour: &Tour<Array>, source: String| -> anyhow::Result<()> {
        tour.check(&instance.matrix)
            .map_err(|message| anyhow::anyhow!("{}: state {name}: {message}", instance.name))?;
        let order = tour.order();
        let duplicate_of = states
            .iter()
            .find(|state| state.order == order)
            .map(|state| state.name.clone());
        let length = tour.length();
        states.push(StateRecord {
            gap: instance
                .optimum
                .map(|optimum| (length - optimum) as f64 / optimum as f64),
            length: length as f64,
            name,
            source,
            order,
            duplicate_of,
        });
        Ok(())
    };
    add(
        "random".into(),
        &Tour::new(&random, &instance.matrix),
        "random permutation".into(),
    )?;
    let start = (seed % n as u64) as City;
    let greedy = nearest_neighbour_tour(&instance.matrix, start);
    add(
        "nearest_neighbour".into(),
        &Tour::new(&greedy, &instance.matrix),
        format!("nearest neighbour from city {start}"),
    )?;
    let mut full: Tour<Array> = Tour::new(&greedy, &instance.matrix);
    let total = -descend(&mut full, &problem, &mut Work::default(), &visit);
    full.clear_log();
    for (name, fraction) in PARTIAL_DESCENTS {
        let mut partial: Tour<Array> = Tour::new(&greedy, &instance.matrix);
        let stop = (fraction * total as f64).ceil() as i64;
        descend_until(
            &mut partial,
            &problem,
            &mut Work::default(),
            &visit,
            stop.max(1),
        );
        add(
            name.into(),
            &partial,
            format!(
                "O7 descent from nearest neighbour stopped at {:.0}% of its full gain",
                fraction * 100.0
            ),
        )?;
    }
    add(
        "opt2".into(),
        &full,
        "full O7 descent from the nearest-neighbour tour".into(),
    )?;
    let mut tour = full;
    vnd(&mut tour, &problem, &visit, &[Op::OrOptNl]);
    add("oropt".into(), &tour, "VND (O7, O9) from opt2".into())?;
    vnd(&mut tour, &problem, &visit, &[Op::OrOptNl, Op::ThreeOptNl]);
    add("opt3".into(), &tour, "VND (O7, O9, O10) from oropt".into())?;
    let lk = Op::lin_kernighan_for(&problem);
    let lk_ops = [Op::OrOptNl, Op::ThreeOptNl, lk];
    vnd(&mut tour, &problem, &visit, &lk_ops);
    add(
        "lkopt".into(),
        &tour,
        format!("VND (O7, O9, O10, {}) from opt3", lk.name()),
    )?;
    let mut kick_rng = Stream::new(seed, "e1-states-ils", &[label_hash(&instance.name)]);
    let mut done = 0;
    for (name, rounds) in ILS_ROUNDS {
        let mut work = Work::default();
        for _ in 0..rounds * n {
            Op::Kick.apply(&mut tour, &problem, &mut kick_rng, &mut work, None);
            tour.clear_log();
        }
        done += rounds;
        vnd(&mut tour, &problem, &visit, &lk_ops);
        add(
            name.into(),
            &tour,
            format!("lkopt after {done}n O8 kick trials, then LK VND"),
        )?;
    }
    let built: Vec<&str> = states.iter().map(|state| state.name.as_str()).collect();
    anyhow::ensure!(
        built == STATE_NAMES,
        "built states {built:?} differ from STATE_NAMES"
    );
    Ok(StatesFile {
        instance: instance.name.clone(),
        path: path.to_string(),
        n,
        optimum: instance.optimum.map(|value| value as f64),
        state_seed: seed,
        states,
    })
}

/// One (instance, seed) to build.
struct StatesTask {
    path: String,
    name: String,
    seed: u64,
    n: usize,
}

/// Builds every (instance, seed) in parallel and writes the states files
/// to `<directory>/states/`.  Returns the run directory.
///
/// # Errors
///
/// If an instance fails to load or a state fails its check.
pub fn run(
    repository: &Path,
    settings: &Settings,
    out: Option<PathBuf>,
) -> anyhow::Result<PathBuf> {
    let directory = match out {
        Some(directory) => {
            std::fs::create_dir_all(&directory)?;
            directory
        }
        None => new_run_directory(&repository.join("runs"), "e1-states", false)?,
    };
    write_config(
        &directory,
        repository,
        json!({
            "experiment": "E1 states by local-optimality level",
            "settings": settings,
            "fractions": PARTIAL_DESCENTS.map(|(_, fraction)| fraction),
            "ils_rounds": ILS_ROUNDS.map(|(_, rounds)| rounds),
        }),
    )?;
    let states_directory = directory.join("states");
    std::fs::create_dir_all(&states_directory)?;
    let mut tasks = Vec::new();
    for path in &settings.instances {
        let file = crate::instance::tsplib::read(&repository.join(path))?;
        let seeds = if file.dimension <= settings.single_seed_above {
            &settings.seeds[..]
        } else {
            &settings.seeds[..1]
        };
        tasks.extend(seeds.iter().map(|&seed| StatesTask {
            path: path.clone(),
            name: file.name.clone(),
            seed,
            n: file.dimension,
        }));
    }
    // Large instances first, so the longest tasks do not finish last.
    tasks.sort_by_key(|task| (std::cmp::Reverse(task.n), task.path.clone(), task.seed));
    let workers = untimed_workers(settings.workers);
    log::info!(
        "building {} (instance, seed) pairs on {workers} threads",
        tasks.len()
    );
    parallel_map(
        &tasks,
        workers,
        |task| format!("{} seed {}", task.path, task.seed),
        |task| {
            // Named by the TSPLIB `NAME`, which the states file records.
            let key = format!("{}_s{}", task.name, task.seed);
            let target = states_directory.join(format!("{key}.json"));
            if target.is_file() {
                return Ok(());
            }
            let started = std::time::Instant::now();
            let instance = load_instance(repository, &task.path)?;
            let file = build(&instance, &task.path, task.seed)?;
            write_atomic(&target, (serde_json::to_string(&file)? + "\n").as_bytes())?;
            log::info!(
                "{key}: {} states in {:.1} s",
                file.states.len(),
                started.elapsed().as_secs_f64()
            );
            Ok(())
        },
    )?;
    Ok(directory)
}
