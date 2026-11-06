//! The required tests of `ideas/rust-port.md` §11.1 for tours and
//! operators, on small random instances (debug builds run them quickly).

use aos::City;
use aos::instance::alpha::{AscentSettings, alpha_candidates, ascend};
use aos::instance::neighbours::{ALPHA_CANDIDATES, CandidateLists, NEIGHBOURS};
use aos::instance::storage::{Computed, Dist, Matrix};
use aos::instance::tour_length;
use aos::instance::tsplib::CoordinateKind;
use aos::ops::{Op, Problem, Work, descend, improve_city};
use aos::rng::Stream;
use aos::tour::array::Array;
use aos::tour::blocks::Blocks;
use aos::tour::{Layout, Tour};

/// A random Euclidean instance with its candidate lists.
struct Fixture {
    computed: Computed,
    matrix: Matrix,
    neighbours: CandidateLists,
    alpha: CandidateLists,
}

impl Fixture {
    fn new(n: usize, seed: u64) -> Self {
        let mut rng = Stream::new(seed, "fixture", &[n as u64]);
        let points = (0..n)
            .map(|_| [rng.index(1000) as f64, rng.index(1000) as f64])
            .collect();
        let computed = Computed::new(CoordinateKind::Euc2d, points);
        let matrix = Matrix::from_computed(&computed).unwrap();
        let neighbours = CandidateLists::nearest(&matrix, NEIGHBOURS);
        let pi = ascend(&matrix, AscentSettings::default());
        let alpha =
            CandidateLists::from_lists(&alpha_candidates(&matrix, &pi, ALPHA_CANDIDATES), &matrix)
                .unwrap();
        Self {
            computed,
            matrix,
            neighbours,
            alpha,
        }
    }

    fn problem<const C: bool>(&self) -> Problem<'_, Matrix, C> {
        Problem {
            dist: &self.matrix,
            neighbours: &self.neighbours,
            alpha: Some(&self.alpha),
        }
    }

    fn random_order(&self, seed: u64) -> Vec<City> {
        let mut order: Vec<City> = (0..self.matrix.n() as City).collect();
        Stream::new(seed, "order", &[]).shuffle(&mut order);
        order
    }
}

const SIZES: [usize; 4] = [12, 23, 40, 64];

#[test]
fn layouts_agree_under_random_reversals_and_swaps() {
    for n in [8, 9, 17, 50, 101] {
        let order: Vec<City> = (0..n as City).collect();
        let mut array = Array::from_order(&order);
        let mut blocks = Blocks::from_order(&order);
        let mut rng = Stream::new(1, "flips", &[n as u64]);
        for step in 0..400 {
            let a = rng.index(n) as City;
            let b = rng.index(n) as City;
            if a == b {
                continue;
            }
            if step % 7 == 0 {
                assert_eq!(array.swap(a, b), 2);
                blocks.swap(a, b);
            } else {
                if array.next(b) == a {
                    continue; // the path a .. b would be the whole tour
                }
                array.reverse(a, b);
                blocks.reverse(a, b);
            }
            blocks.check_consistent().unwrap();
            array.check_consistent().unwrap();
            for city in 0..n as City {
                assert_eq!(array.next(city), blocks.next(city), "n={n} step={step}");
                assert_eq!(array.prev(city), blocks.prev(city));
            }
            let probe = rng.index(n) as City;
            let k = rng.index(n);
            assert_eq!(array.walk(probe, k), blocks.walk(probe, k));
            assert_eq!(array.steps(a, b), blocks.steps(a, b));
        }
    }
}

#[test]
fn reversal_reverses_exactly_the_path() {
    let n = 10;
    let order: Vec<City> = (0..n).collect();
    for (a, b) in [(2, 5), (7, 1), (0, 8), (9, 0)] {
        let mut layout = Blocks::from_order(&order);
        layout.reverse(a, b);
        // prev(a) is now followed by b, and a by the old next(b).
        assert_eq!(layout.next((a + n - 1) % n), b);
        assert_eq!(layout.next(a), (b + 1) % n);
        let mut array = Array::from_order(&order);
        array.reverse(a, b);
        assert_eq!(array.next((a + n - 1) % n), b);
    }
}

#[test]
fn undo_restores_the_exact_logical_tour() {
    let fixture = Fixture::new(40, 3);
    let order = fixture.random_order(4);
    let mut tour: Tour<Blocks> = Tour::new(&order, &fixture.matrix);
    let original = tour.clone();
    let mark = tour.mark();
    let mut rng = Stream::new(5, "undo", &[]);
    for _ in 0..100 {
        let a = rng.index(40) as City;
        let b = rng.index(40) as City;
        if a != b && tour.next(b) != a {
            tour.reverse(a, b);
        }
    }
    tour.replace(&fixture.random_order(6));
    tour.swap(1, 2);
    tour.undo_to(mark);
    assert!(tour.same_as(&original));
    assert_eq!(tour.length(), original.length());
}

/// Applies `op` at every anchor (a few repeats) from `order` and checks the
/// operator contract; returns every (delta, hash) for comparisons.
fn contract<L: Layout, D: Dist, const C: bool>(
    op: Op,
    order: &[City],
    problem: &Problem<'_, D, C>,
    dist: &impl Dist,
) -> Vec<(i64, u64)> {
    let n = order.len();
    let original: Tour<L> = Tour::new(order, dist);
    let mut outcomes = Vec::new();
    for anchor in 0..n as City {
        for repeat in 0..3u64 {
            let mut tour = original.clone();
            let mut rng = Stream::new(9, "contract", &[op as u64, u64::from(anchor), repeat]);
            let mut work = Work::default();
            let application = op.apply(&mut tour, problem, &mut rng, &mut work, Some(anchor));
            assert!(application.delta <= 0, "{} lengthened the tour", op.name());
            assert_eq!(
                tour_length(dist, &tour.order()),
                original.length() + application.delta,
                "{} at {anchor}: returned change differs from the real one",
                op.name()
            );
            tour.check(dist)
                .unwrap_or_else(|error| panic!("{}: {error}", op.name()));
            if application.delta == 0 {
                assert!(
                    tour.same_as(&original),
                    "{} changed the tour without improving",
                    op.name()
                );
            }
            assert_eq!(application.anchor, anchor);
            outcomes.push((application.delta, tour.hash()));
            if op.deterministic() {
                break;
            }
        }
    }
    outcomes
}

#[test]
fn operator_contract_holds_and_layouts_agree() {
    for (index, &n) in SIZES.iter().enumerate() {
        let fixture = Fixture::new(n, index as u64);
        let problem = fixture.problem::<true>();
        for tour_seed in 0..2 {
            let order = fixture.random_order(tour_seed);
            for op in Op::ALL {
                let array = contract::<Array, _, true>(op, &order, &problem, &fixture.matrix);
                assert!(
                    array.iter().any(|&(delta, _)| delta < 0),
                    "{} never improved a random tour (n={n}): the test would be vacuous",
                    op.name()
                );
                let blocks = contract::<Blocks, _, true>(op, &order, &problem, &fixture.matrix);
                assert_eq!(
                    array,
                    blocks,
                    "{} differs between layouts (n={n})",
                    op.name()
                );
            }
        }
    }
}

#[test]
fn deterministic_gains_agree_across_storage_and_cache_modes() {
    let fixture = Fixture::new(40, 11);
    let order = fixture.random_order(12);
    let cached = fixture.problem::<true>();
    let uncached = fixture.problem::<false>();
    let computed: Problem<'_, Computed, true> = Problem {
        dist: &fixture.computed,
        neighbours: &fixture.neighbours,
        alpha: Some(&fixture.alpha),
    };
    for op in Op::ALL.into_iter().filter(|op| op.deterministic()) {
        let main = contract::<Array, _, true>(op, &order, &cached, &fixture.matrix);
        assert_eq!(
            main,
            contract::<Array, _, false>(op, &order, &uncached, &fixture.matrix),
            "{}",
            op.name()
        );
        assert_eq!(
            main,
            contract::<Blocks, _, true>(op, &order, &computed, &fixture.computed),
            "{}",
            op.name()
        );
    }
}

#[test]
fn work_counters_split_cache_and_matrix_reads() {
    let fixture = Fixture::new(40, 13);
    let order = fixture.random_order(14);
    let mut tour: Tour<Array> = Tour::new(&order, &fixture.matrix);
    let mut work = Work::default();
    improve_city(
        &mut tour,
        &fixture.problem::<true>(),
        &fixture.neighbours,
        0,
        &mut work,
    );
    assert!(work.cache_reads > 0);
    let mut tour: Tour<Array> = Tour::new(&order, &fixture.matrix);
    let mut uncached = Work::default();
    improve_city(
        &mut tour,
        &fixture.problem::<false>(),
        &fixture.neighbours,
        0,
        &mut uncached,
    );
    assert_eq!(uncached.cache_reads, 0);
    assert_eq!(uncached.lookups(), work.lookups());
}

/// Every improving neighbour-list 2-opt move, enumerated independently of
/// `improve_city`.
fn improving_nl_two_opt(
    tour: &Tour<Array>,
    dist: &Matrix,
    lists: &CandidateLists,
) -> Option<(City, City)> {
    for city in 0..tour.n() as City {
        for forward in [true, false] {
            let partner = tour.succ_in(city, forward);
            for &candidate in lists.cities(city) {
                let other = tour.succ_in(candidate, forward);
                if candidate == partner
                    || other == city
                    || dist.d(city, candidate) >= dist.d(city, partner)
                {
                    continue;
                }
                let delta = dist.d(city, candidate) + dist.d(partner, other)
                    - dist.d(city, partner)
                    - dist.d(candidate, other);
                if delta < 0 {
                    return Some((city, candidate));
                }
            }
        }
    }
    None
}

#[test]
fn local_optima_have_no_improving_application() {
    for (index, &n) in SIZES.iter().enumerate() {
        let fixture = Fixture::new(n, 20 + index as u64);
        let problem = fixture.problem::<true>();
        let order = fixture.random_order(21);
        let mut tour: Tour<Array> = Tour::new(&order, &fixture.matrix);
        let all: Vec<City> = (0..n as City).collect();
        descend(&mut tour, &problem, &mut Work::default(), &all, None);
        assert_eq!(
            improving_nl_two_opt(&tour, &fixture.matrix, &fixture.neighbours),
            None
        );
        // Descend each operator to its local optimum, then confirm no
        // anchor improves.
        for op in [
            Op::TwoOptFirst,
            Op::TwoOptNl,
            Op::OrOptNl,
            Op::ThreeOptNl,
            Op::LinKernighan,
            Op::LinKernighanAlpha,
        ] {
            let mut work = Work::default();
            let mut rng = Stream::new(0, "optimum", &[]);
            loop {
                let improved = (0..n as City)
                    .filter(|&city| {
                        op.apply(&mut tour, &problem, &mut rng, &mut work, Some(city))
                            .delta
                            < 0
                    })
                    .count();
                if improved == 0 {
                    break;
                }
            }
            for city in 0..n as City {
                let before = tour.clone();
                assert_eq!(
                    op.apply(&mut tour, &problem, &mut rng, &mut work, Some(city))
                        .delta,
                    0
                );
                assert!(tour.same_as(&before));
            }
        }
        // A full-scan 2-opt optimum: no improving pair at all.
        for a in 0..n as City {
            let b = tour.next(a);
            for c in 0..n as City {
                let d = tour.next(c);
                if c == a || c == b || d == a {
                    continue;
                }
                let m = &fixture.matrix;
                assert!(
                    m.d(a, c) + m.d(b, d) >= m.d(a, b) + m.d(c, d),
                    "improving 2-opt move left"
                );
            }
        }
    }
}

#[test]
fn replays_match_on_any_thread_in_any_order() {
    let fixture = Fixture::new(40, 30);
    let problem = fixture.problem::<true>();
    let order = fixture.random_order(31);
    let original: Tour<Array> = Tour::new(&order, &fixture.matrix);
    let run = |op: Op, anchor: City, repeat: u64| {
        let mut tour = original.clone();
        let mut rng = Stream::new(
            7,
            "kroA100:s0:opt2",
            &[op as u64, u64::from(anchor), repeat],
        );
        let application = op.apply(
            &mut tour,
            &problem,
            &mut rng,
            &mut Work::default(),
            Some(anchor),
        );
        (application.delta, tour.hash())
    };
    let jobs: Vec<(Op, City, u64)> = Op::ALL
        .into_iter()
        .flat_map(|op| (0..40).step_by(7).map(move |anchor| (op, anchor, 2)))
        .collect();
    let forward: Vec<_> = jobs
        .iter()
        .map(|&(op, anchor, repeat)| run(op, anchor, repeat))
        .collect();
    let mut backward: Vec<_> = std::thread::scope(|scope| {
        scope
            .spawn(|| {
                jobs.iter()
                    .rev()
                    .map(|&(op, anchor, repeat)| run(op, anchor, repeat))
                    .collect::<Vec<_>>()
            })
            .join()
            .unwrap()
    });
    backward.reverse();
    assert_eq!(forward, backward);
}

#[test]
fn operator_names_round_trip() {
    for op in Op::ALL {
        assert_eq!(Op::from_name(op.name()), Some(op));
    }
    assert_eq!(Op::LkKick.id(), "O21");
    assert_eq!(Op::ALL.iter().filter(|op| op.deterministic()).count(), 8);
}

#[test]
fn retirement_keeps_protocol_ids_and_skips_retired_names() {
    // O2-O4, O6 and O20 were retired; every other operator keeps its id.
    let ids: Vec<String> = Op::ALL.iter().map(|op| op.id()).collect();
    let expected = [1, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21];
    assert_eq!(
        ids,
        expected.map(|number| format!("O{number}")).to_vec(),
        "protocol ids moved"
    );
    for (position, op) in Op::ALL.into_iter().enumerate() {
        assert_eq!(op.index(), position);
    }
    for name in aos::ops::RETIRED {
        assert!(Op::from_name(name).is_none(), "{name} is still an operator");
        assert!(Op::parse_stored(name).unwrap().is_none());
    }
    assert_eq!(Op::parse_stored("swap").unwrap(), Some(Op::Swap));
    assert!(Op::parse_stored("no_such_operator").is_err());
}
