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
//! Python's (`rust/DEVIATIONS.md` D10), so the tours do too; to measure the
//! Python tours exactly, point measurement A at the Python states.

use std::path::{Path, PathBuf};

use serde::Serialize;
use serde_json::json;

use super::common::{StateRecord, StatesFile, all_cpus, load_instance, parallel_map};
use crate::City;
use crate::instance::Instance;
use crate::instance::storage::{Dist, Matrix};
use crate::io::provenance::write_config;
use crate::io::rundir::{new_run_directory, write_atomic};
use crate::ops::{Op, Problem, Work, descend, improve_city};
use crate::rng::{Stream, label_hash};
use crate::tour::Tour;
use crate::tour::array::Array;

/// Shares of the full descent's gain at which partial descents stop.
pub const FRACTIONS: [f64; 4] = [0.25, 0.5, 0.75, 0.9];

/// Kick trials per round of iterated local search, in multiples of n.
pub const ILS_ROUNDS: [usize; 2] = [1, 4];

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
pub fn nearest_neighbour_tour(matrix: &Matrix, start: City) -> Vec<City> {
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
            if improve_city(tour, problem, problem.neighbours, city, &mut work).0 < 0 {
                improved = true;
            }
            for op in ops {
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
    let problem: Problem<'_, Matrix, true> = Problem {
        dist: &instance.matrix,
        neighbours: &instance.neighbours,
        alpha: instance.alpha.as_ref(),
    };
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
    let total = -descend(&mut full, &problem, &mut Work::default(), &visit, None);
    full.clear_log();
    for fraction in FRACTIONS {
        let mut partial: Tour<Array> = Tour::new(&greedy, &instance.matrix);
        let stop = (fraction * total as f64).ceil() as i64;
        descend(
            &mut partial,
            &problem,
            &mut Work::default(),
            &visit,
            Some(stop.max(1)),
        );
        add(
            format!("d{:02}", (fraction * 100.0).round() as u32),
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
    let lk = if instance.alpha.is_some() {
        Op::LinKernighanAlpha
    } else {
        Op::LinKernighan
    };
    let lk_ops = [Op::OrOptNl, Op::ThreeOptNl, lk];
    vnd(&mut tour, &problem, &visit, &lk_ops);
    add(
        "lkopt".into(),
        &tour,
        format!("VND (O7, O9, O10, {}) from opt3", lk.name()),
    )?;
    let mut kick_rng = Stream::new(seed, "e1-states-ils", &[label_hash(&instance.name)]);
    let mut done = 0;
    for (index, rounds) in ILS_ROUNDS.iter().enumerate() {
        let mut work = Work::default();
        for _ in 0..rounds * n {
            Op::Kick.apply(&mut tour, &problem, &mut kick_rng, &mut work, None);
            tour.clear_log();
        }
        done += rounds;
        vnd(&mut tour, &problem, &visit, &lk_ops);
        add(
            format!("ils{}", index + 1),
            &tour,
            format!("lkopt after {done}n O8 kick trials, then LK VND"),
        )?;
    }
    Ok(StatesFile {
        instance: instance.name.clone(),
        path: path.to_string(),
        n,
        optimum: instance.optimum.map(|value| value as f64),
        state_seed: seed,
        states,
    })
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
            "fractions": FRACTIONS,
            "ils_rounds": ILS_ROUNDS,
        }),
    )?;
    let states_directory = directory.join("states");
    std::fs::create_dir_all(&states_directory)?;
    let mut tasks = Vec::new();
    for path in &settings.instances {
        let n = crate::instance::tsplib::read(&repository.join(path))?.dimension;
        let seeds = if n <= settings.single_seed_above {
            &settings.seeds[..]
        } else {
            &settings.seeds[..1]
        };
        tasks.extend(seeds.iter().map(|&seed| (path.clone(), seed, n)));
    }
    // Large instances first, so the longest tasks do not finish last.
    tasks.sort_by_key(|(path, seed, n)| (std::cmp::Reverse(*n), path.clone(), *seed));
    let workers = if settings.workers == 0 {
        all_cpus()
    } else {
        settings.workers
    };
    log::info!(
        "building {} (instance, seed) pairs on {workers} threads",
        tasks.len()
    );
    parallel_map(
        &tasks,
        workers,
        |(path, seed, _)| format!("{path} seed {seed}"),
        |(path, seed, _)| {
            let target = states_directory.join(format!(
                "{}_s{seed}.json",
                Path::new(path)
                    .file_stem()
                    .and_then(|stem| stem.to_str())
                    .unwrap_or("instance")
            ));
            if target.is_file() {
                return Ok(());
            }
            let started = std::time::Instant::now();
            let instance = load_instance(repository, path)?;
            let file = build(&instance, path, *seed)?;
            let target = states_directory.join(format!("{}_s{seed}.json", file.instance));
            write_atomic(&target, (serde_json::to_string(&file)? + "\n").as_bytes())?;
            log::info!(
                "{}_s{seed}: {} states in {:.1} s",
                file.instance,
                file.states.len(),
                started.elapsed().as_secs_f64()
            );
            Ok(())
        },
    )?;
    Ok(directory)
}
