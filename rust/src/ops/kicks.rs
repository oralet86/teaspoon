//! Kicks repaired by a descent: O8, O12, O13 and O21.
//!
//! Python builds each kick on a copy of the array and keeps or drops the
//! copy.  Here the kick is applied to the tour as reversals, the descent
//! runs in place, and a trial that does not improve is undone through the
//! undo log: the logical tour is then exactly what it was.

use super::lk::lk_step;
use super::two_opt::descend;
use super::{Application, Problem, Work, WorkQueue, pick_anchor, reverse};
use crate::City;
use crate::instance::storage::Dist;
use crate::rng::Stream;
use crate::tour::{Layout, Tour};

/// The segment exchange of O8 and O21, applied to the tour.
///
/// Reading the tour from the anchor's successor as X Y R (R ends with the
/// anchor), with cuts drawn as in Python, the result is R Y X: three edges
/// replaced, a pure sequential 3-opt move.  The length change is added to
/// the tour; returns the six cities at the cut edges.
fn segment_kick<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: City,
) -> [City; 6] {
    let n = tour.n();
    let mut cuts = rng.sample(n - 1, 2);
    cuts.sort_unstable();
    let (cut_2, cut_3) = (cuts[0] + 1, cuts[1] + 1);
    let x_start = tour.next(anchor);
    let x_end = tour.walk(x_start, cut_2 - 1);
    let y_start = tour.walk(x_start, cut_2);
    let y_end = tour.walk(x_start, cut_3 - 1);
    let r_start = tour.walk(x_start, cut_3);
    let delta = problem.d(work, anchor, y_start)
        + problem.d(work, y_end, x_start)
        + problem.d(work, x_end, r_start)
        - problem.d(work, anchor, x_start)
        - problem.d(work, x_end, y_start)
        - problem.d(work, y_end, r_start);
    // X Y -> X' Y' -> (X' Y')' = Y X.
    reverse(tour, work, x_start, x_end);
    reverse(tour, work, y_start, y_end);
    reverse(tour, work, x_end, y_start);
    tour.add_length(delta);
    [anchor, x_start, x_end, y_start, y_end, r_start]
}

/// Keeps the trial since `mark` if the tour got shorter, else undoes it.
/// Returns the change kept.
fn keep_if_shorter<L: Layout>(
    tour: &mut Tour<L>,
    work: &mut Work,
    mark: crate::tour::Mark,
    before: i64,
) -> i64 {
    let delta = tour.length() - before;
    if delta < 0 {
        return delta;
    }
    work.positions += tour.undo_to(mark);
    0
}

/// O8: a segment exchange cutting the edge after the anchor and two random
/// other edges, then O7 descent from the cities it touched; kept only if
/// the tour got shorter.
pub(super) fn kick<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let city = pick_anchor(tour.n(), rng, anchor);
    let mark = tour.mark();
    let before = tour.length();
    let touched = segment_kick(tour, problem, rng, work, city);
    descend(tour, problem, work, &touched);
    let delta = keep_if_shorter(tour, work, mark, before);
    Application {
        delta,
        anchor: city,
    }
}

/// Applies the double bridge at the four cities `cuts` (in forward order
/// along the tour) and adds its length change, which it returns.
///
/// With segments S1 = (c1 .. c2], S2 = (c2 .. c3], S3 = (c3 .. c4] and S0
/// the rest, the tour S0 S1 S2 S3 becomes S0 S3 S2 S1: all four cut edges
/// are replaced (Martin, Otto and Felten 1991), the same edges as
/// Concorde's random four-swap kick (`linkern.c`, `random_four_swap`).
pub fn double_bridge_move<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    work: &mut Work,
    cuts: [City; 4],
) -> i64 {
    let [a1, a2, a3, a4] = cuts;
    let [b1, b2, b3, b4] = cuts.map(|city| tour.next(city));
    let delta = problem.d(work, a1, b3)
        + problem.d(work, a4, b2)
        + problem.d(work, a3, b1)
        + problem.d(work, a2, b4)
        - problem.d(work, a1, b1)
        - problem.d(work, a2, b2)
        - problem.d(work, a3, b3)
        - problem.d(work, a4, b4);
    // a1 [S1 S2 S3] b4 -> a1 [S3' S2' S1'] b4 -> a1 [S3 S2 S1] b4.
    reverse(tour, work, b1, a4);
    reverse(tour, work, a4, b3);
    reverse(tour, work, a3, b2);
    reverse(tour, work, a2, b1);
    tour.add_length(delta);
    delta
}

/// The double bridge at `cuts`, then an O7 descent from the eight cities
/// at the cut edges; kept only if shorter.  Returns the change kept.
fn double_bridge_trial<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    work: &mut Work,
    cuts: [City; 4],
) -> i64 {
    let mark = tour.mark();
    let before = tour.length();
    let ends = cuts.map(|city| tour.next(city));
    double_bridge_move(tour, problem, work, cuts);
    let start = [
        cuts[0], cuts[1], cuts[2], cuts[3], ends[0], ends[1], ends[2], ends[3],
    ];
    descend(tour, problem, work, &start);
    keep_if_shorter(tour, work, mark, before)
}

/// Orders `others` along the tour from `anchor` and returns the four cuts.
fn in_tour_order<L: Layout>(tour: &Tour<L>, anchor: City, mut others: [City; 3]) -> [City; 4] {
    others.sort_unstable_by_key(|&city| tour.steps(anchor, city));
    [anchor, others[0], others[1], others[2]]
}

/// O12: a double bridge cutting the edge after the anchor and three random
/// other edges, then O7 descent; kept only if shorter.
pub(super) fn double_bridge<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let n = tour.n();
    let city = pick_anchor(n, rng, anchor);
    let offsets = rng.sample(n - 1, 3);
    let others = [0, 1, 2].map(|k| tour.walk(city, offsets[k] + 1));
    let cuts = in_tour_order(tour, city, others);
    let delta = double_bridge_trial(tour, problem, work, cuts);
    Application {
        delta,
        anchor: city,
    }
}

/// O13: a double bridge cutting the edges after the anchor and three random
/// cities of its neighbour list, then O7 descent; kept only if shorter.
pub(super) fn double_bridge_local<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let city = pick_anchor(tour.n(), rng, anchor);
    let neighbours = problem.neighbours.cities(city);
    let picks = rng.sample(neighbours.len(), 3);
    let others = [0, 1, 2].map(|k| neighbours[picks[k]]);
    let cuts = in_tour_order(tour, city, others);
    let delta = double_bridge_trial(tour, problem, work, cuts);
    Application {
        delta,
        anchor: city,
    }
}

/// O21: O8's segment exchange (the same random cuts) repaired by an LK
/// descent instead of O7: a queue of the cities the kick touched, each
/// given the LK step of O18 (alpha candidates) or O16 (without them); the
/// cities an improving step touches re-enter the queue.  Kept only if
/// shorter (chained LK, Applegate, Cook and Rohe 2003).
pub(super) fn lk_kick<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let city = pick_anchor(tour.n(), rng, anchor);
    let lists = problem.lin_kernighan_lists();
    let mark = tour.mark();
    let before = tour.length();
    let touched = segment_kick(tour, problem, rng, work, city);
    let mut queue = WorkQueue::new(tour.n(), touched);
    let mut changed = Vec::new();
    while let Some(current) = queue.pop() {
        let step_mark = tour.mark();
        if lk_step(tour, problem, lists, current, work) < 0 {
            changed.clear();
            tour.touched_since(step_mark, &mut changed);
            queue.extend(changed.iter().copied());
        }
    }
    let delta = keep_if_shorter(tour, work, mark, before);
    Application {
        delta,
        anchor: city,
    }
}
