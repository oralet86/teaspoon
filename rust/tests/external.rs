//! Cross-checks against independent, published implementations.
//!
//! - Concorde's two-level-list flipper (`LINKERN/flip_two.c`), through a
//!   small C harness that applies flips and prints `next[]` after each:
//!   set `CONCORDE_FLIP_HARNESS` to its path.
//! - Concorde's random double-bridge kick (`linkern.c`,
//!   `random_four_swap`): its three flips and its length change, replayed
//!   with the reversal semantics the harness test establishes.
//!
//! Run with `cargo test --test external -- --ignored`.
//!
//! The flipper harness, linked against a built Concorde tree, reads `n m`
//! and then `m` lines `x y` from stdin, applies
//! `CClinkern_flipper_flip(F, x, y)` to the identity cycle for each, and
//! prints `CClinkern_flipper_next` of every city after each flip.
//! LKH output for the alpha check: run `vendor/LKH` on a parameter file with
//! `MAX_CANDIDATES = 5`, `CANDIDATE_FILE = <dir>/<name>.cand`,
//! `PI_FILE = <dir>/<name>.pi`, `RUNS = 1`, `MAX_TRIALS = 1`.

use std::io::Write;
use std::process::{Command, Stdio};

use aos::City;
use aos::instance::neighbours::{CandidateLists, NEIGHBOURS};
use aos::instance::storage::{Computed, Dist, Matrix};
use aos::instance::tour_length;
use aos::instance::tsplib::CoordinateKind;
use aos::ops::{Problem, Work, double_bridge_move};
use aos::rng::Stream;
use aos::tour::array::Array;
use aos::tour::blocks::Blocks;
use aos::tour::{Layout, Tour};

#[test]
#[ignore = "needs CONCORDE_FLIP_HARNESS (a build of Concorde's flip_two.c)"]
fn reversals_match_concordes_two_level_flipper() {
    let harness = std::env::var("CONCORDE_FLIP_HARNESS").expect("set CONCORDE_FLIP_HARNESS");
    for n in [8usize, 13, 50, 101, 1000] {
        // Draw flips on our layout, skipping whole-tour paths.
        let identity: Vec<City> = (0..n as City).collect();
        let mut array = Array::from_order(&identity);
        let mut blocks = Blocks::from_order(&identity);
        let mut rng = Stream::new(42, "concorde", &[n as u64]);
        let mut flips = Vec::new();
        let mut expected = Vec::new();
        while flips.len() < 300 {
            let x = rng.index(n) as City;
            let y = rng.index(n) as City;
            if x == y || array.next(y) == x {
                continue;
            }
            array.reverse(x, y);
            blocks.reverse(x, y);
            flips.push((x, y));
            let next: Vec<City> = (0..n as City).map(|c| array.next(c)).collect();
            let block_next: Vec<City> = (0..n as City).map(|c| blocks.next(c)).collect();
            assert_eq!(next, block_next);
            expected.push(next);
        }
        let mut child = Command::new(&harness)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .expect("harness starts");
        let mut input = format!("{n} {}\n", flips.len());
        for (x, y) in &flips {
            input.push_str(&format!("{x} {y}\n"));
        }
        child
            .stdin
            .take()
            .unwrap()
            .write_all(input.as_bytes())
            .unwrap();
        let output = child.wait_with_output().unwrap();
        assert!(output.status.success());
        let text = String::from_utf8(output.stdout).unwrap();
        for (step, (line, ours)) in text.lines().zip(&expected).enumerate() {
            let theirs: Vec<City> = line
                .split_whitespace()
                .map(|v| v.parse().unwrap())
                .collect();
            assert_eq!(
                &theirs, ours,
                "n={n}: next[] differs from Concorde after flip {step} {:?}",
                flips[step]
            );
        }
        assert_eq!(text.lines().count(), flips.len());
    }
}

#[test]
fn double_bridge_matches_concordes_four_swap() {
    let n = 60;
    let mut rng = Stream::new(3, "bridge", &[]);
    let points = (0..n)
        .map(|_| [rng.index(1000) as f64, rng.index(1000) as f64])
        .collect();
    let computed = Computed::new(CoordinateKind::Euc2d, points);
    let matrix = Matrix::from_computed(&computed).unwrap();
    let neighbours = CandidateLists::nearest(&matrix, NEIGHBOURS);
    let problem: Problem<'_, Matrix, true> = Problem {
        dist: &matrix,
        neighbours: &neighbours,
        alpha: None,
    };
    for trial in 0..200 {
        let mut order: Vec<City> = (0..n as City).collect();
        rng.shuffle(&mut order);
        let tour: Tour<Array> = Tour::new(&order, &matrix);
        // Four distinct cities in tour order: Concorde's t1, t3, t5, t7.
        let mut offsets = rng.sample(n, 4);
        offsets.sort_unstable();
        let cuts: [City; 4] = [0, 1, 2, 3].map(|k| tour.walk(order[0], offsets[k]));
        let [t1, t3, t5, t7] = cuts;
        let [t2, t4, t6, t8] = cuts.map(|c| tour.next(c));
        let d = |a: City, b: City| i64::from(matrix.d(a, b));
        let concorde_delta = d(t1, t6) + d(t2, t5) + d(t3, t8) + d(t4, t7)
            - d(t1, t2)
            - d(t3, t4)
            - d(t5, t6)
            - d(t7, t8);
        // Concorde: FLIP(t1,t2,t5,t6); FLIP(t4,t3,t7,t8); FLIP(t1,t5,t6,t2),
        // where FLIP(_, a, b, _) reverses the path a .. b.
        let mut theirs = tour.clone();
        theirs.reverse(t2, t5);
        theirs.reverse(t3, t7);
        theirs.reverse(t5, t6);
        let mut ours = tour.clone();
        let delta = double_bridge_move(&mut ours, &problem, &mut Work::default(), cuts);
        assert_eq!(delta, concorde_delta, "trial {trial}");
        assert_eq!(
            ours.hash(),
            theirs.hash(),
            "trial {trial}: different cycles"
        );
        assert_eq!(tour_length(&matrix, &ours.order()), tour.length() + delta);
    }
}

/// Reads LKH's `CANDIDATE_FILE` as zero-based candidate sets.
fn lkh_candidates(path: &std::path::Path) -> Vec<Vec<City>> {
    let text = std::fs::read_to_string(path).unwrap();
    let mut lines = text.lines();
    let n: usize = lines.next().unwrap().trim().parse().unwrap();
    let mut sets = vec![Vec::new(); n];
    for line in lines.take(n) {
        let values: Vec<i64> = line
            .split_whitespace()
            .map(|v| v.parse().unwrap())
            .collect();
        let count = values[2] as usize;
        let mut set: Vec<City> = (0..count)
            .map(|k| (values[3 + 2 * k] - 1) as City)
            .collect();
        set.sort_unstable();
        sets[(values[0] - 1) as usize] = set;
    }
    sets
}

/// With LKH's own penalties (`PI_FILE`, costs scaled by PRECISION = 100),
/// our alpha computation must pick LKH's candidate sets, apart from LKH's
/// different choice of the special 1-tree node and ties.  LKH drops
/// candidates whose alpha exceeds EXCESS times the bound (fewer than 5 on
/// random matrices), so its set must be contained in ours.  Set
/// `LKH_ALPHA_DIR` to a directory of `<name>.cand`/`<name>.pi` files written
/// by `vendor/LKH` with `MAX_CANDIDATES = 5`.
#[test]
#[ignore = "needs LKH_ALPHA_DIR (CANDIDATE_FILE and PI_FILE output of LKH-3)"]
fn alpha_with_lkh_penalties_matches_lkh() {
    use aos::instance::Instance;
    use aos::instance::alpha::alpha_candidates;
    let directory =
        std::path::PathBuf::from(std::env::var("LKH_ALPHA_DIR").expect("set LKH_ALPHA_DIR"));
    let repository = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    let mut report = Vec::new();
    for entry in std::fs::read_dir(&directory).unwrap().flatten() {
        let path = entry.path();
        if path.extension().is_none_or(|extension| extension != "cand") {
            continue;
        }
        let name = path.file_stem().unwrap().to_str().unwrap().to_string();
        let file = ["ALL_tsp", "generated/tsp"]
            .iter()
            .map(|folder| {
                repository
                    .join("data")
                    .join(folder)
                    .join(format!("{name}.tsp"))
            })
            .find(|candidate| candidate.is_file())
            .unwrap();
        let instance = Instance::load(&file, &repository.join("data/cache/candidates-rs")).unwrap();
        let n = instance.n();
        let scaled = Matrix::new(
            n,
            (0..n as City)
                .flat_map(|i| (0..n as City).map(move |j| (i, j)))
                .map(|(i, j)| instance.matrix.d(i, j) * 100)
                .collect(),
        );
        let mut pi = vec![0.0; n];
        let pi_text = std::fs::read_to_string(directory.join(format!("{name}.pi"))).unwrap();
        for line in pi_text.lines().skip(1) {
            let mut fields = line.split_whitespace();
            let (Some(id), Some(value)) = (fields.next(), fields.next()) else {
                continue;
            };
            pi[id.parse::<usize>().unwrap() - 1] = value.parse::<f64>().unwrap();
        }
        let ours = alpha_candidates(&scaled, &pi, 5);
        let theirs = lkh_candidates(&path);
        let same = (0..n)
            .filter(|&c| {
                theirs[c]
                    .iter()
                    .all(|candidate| ours[c].contains(candidate))
            })
            .count();
        report.push((name, same as f64 / n as f64));
    }
    report.sort_by(|a, b| a.0.cmp(&b.0));
    for (name, share) in &report {
        println!(
            "{name}: {:.1}% of cities have LKH's candidate set",
            100.0 * share
        );
    }
    assert!(!report.is_empty(), "no LKH output found");
    assert!(
        report.iter().all(|(_, share)| *share >= 0.95),
        "alpha sets differ from LKH: {report:?}"
    );
}
