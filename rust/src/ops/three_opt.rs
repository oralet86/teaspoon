//! O10: neighbour-list sequential 3-opt with the LK gain criterion.

use super::{Application, Problem, Work, exchange, pick_anchor};
use crate::City;
use crate::instance::storage::Dist;
use crate::rng::Stream;
use crate::tour::{Layout, Tour};

/// O10: the first improving sequential 3-opt move that removes an edge at
/// the anchor, searched over neighbour lists with the Lin-Kernighan gain
/// criterion (an LK step of depth 3, Johnson and McGeoch 1997).  Improving
/// 2-opt closures at depth 2 are applied as well.
pub(super) fn three_opt_nl<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let city = pick_anchor(tour.n(), rng, anchor);
    for forward in [true, false] {
        let delta = three_opt_at(tour, problem, city, forward, work);
        if delta < 0 {
            return Application {
                delta,
                anchor: city,
            };
        }
    }
    Application {
        delta: 0,
        anchor: city,
    }
}

/// Searches and applies the first improving sequential 3-opt move that
/// removes the edge from `t1` to its neighbour in direction `forward`
/// (`aos.operators._three_opt_at`).
///
/// Notation of Lin and Kernighan: removed edges (t1, t2), (t3, t4),
/// (t5, t6), added edges (t2, t3), (t4, t5), (t6, t1); "succ" and "pred"
/// follow the direction.  Returns the length change, or zero.
fn three_opt_at<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    t1: City,
    forward: bool,
    work: &mut Work,
) -> i64 {
    let lists = problem.neighbours;
    let succ = |tour: &Tour<L>, city: City| tour.succ_in(city, forward);
    let pred = |tour: &Tour<L>, city: City| tour.succ_in(city, !forward);
    // Whether `middle` lies on the path from `first` to `last` in the
    // search direction.
    let between = |tour: &Tour<L>, first: City, middle: City, last: City| {
        if forward {
            tour.between(first, middle, last)
        } else {
            tour.between(last, middle, first)
        }
    };
    let t2 = succ(tour, t1);
    let g0 = problem.d(work, t1, t2);
    for slot in 0..lists.k() {
        let (t3, d23) = problem.candidate(work, lists, t2, slot);
        let g1 = g0 - d23;
        if g1 <= 0 {
            break;
        }
        if t3 == t1 || t3 == succ(tour, t2) {
            continue;
        }
        // t4 = pred(t3): a 2-opt move closes the tour.
        let t4 = pred(tour, t3);
        let open_gain = g1 + problem.d(work, t3, t4);
        let gain = open_gain - problem.d(work, t4, t1);
        if gain > 0 {
            exchange(tour, work, t1, t2, t4, t3);
            tour.add_length(-gain);
            return -gain;
        }
        // After that 2-opt move the tour reads t1 t4 .. t2 t3 .. t1; a
        // second 2-opt move removes (t1, t4) and (t6, t5), t6 before t5.
        let skip = pred(tour, t4);
        for slot5 in 0..lists.k() {
            let (t5, d45) = problem.candidate(work, lists, t4, slot5);
            let g2 = open_gain - d45;
            if g2 <= 0 {
                break;
            }
            if t5 == t1 || t5 == t3 || t5 == skip {
                continue;
            }
            let t6 = if between(tour, t2, t5, t4) {
                succ(tour, t5)
            } else {
                pred(tour, t5)
            };
            let gain = g2 + problem.d(work, t5, t6) - problem.d(work, t6, t1);
            if gain > 0 {
                exchange(tour, work, t1, t2, t4, t3);
                exchange(tour, work, t1, t4, t6, t5);
                tour.add_length(-gain);
                return -gain;
            }
        }
        // t4 = succ(t3): no 2-opt closure; t5 must lie on t2 .. t3, which
        // the first two changes cut off as a separate cycle.
        let t4 = succ(tour, t3);
        if t4 == t1 {
            continue;
        }
        let open_gain = g1 + problem.d(work, t3, t4);
        for slot5 in 0..lists.k() {
            let (t5, d45) = problem.candidate(work, lists, t4, slot5);
            let g2 = open_gain - d45;
            if g2 <= 0 {
                break;
            }
            if t5 == t3 || !between(tour, t2, t5, t3) {
                continue;
            }
            for t6 in [succ(tour, t5), pred(tour, t5)] {
                if t6 == t1 {
                    continue;
                }
                let gain = g2 + problem.d(work, t5, t6) - problem.d(work, t6, t1);
                if gain <= 0 {
                    continue;
                }
                if t6 == succ(tour, t5) {
                    // t1 [t2 .. t5] [t6 .. t3] t4 -> t1 [t6 .. t3] [t2 .. t5] t4
                    exchange(tour, work, t1, t2, t5, t6);
                    exchange(tour, work, t1, t5, t3, t4);
                    exchange(tour, work, t1, t3, t6, t2);
                } else {
                    // t1 [t2 .. t6] [t5 .. t3] t4 -> both segments reversed
                    exchange(tour, work, t1, t2, t6, t5);
                    exchange(tour, work, t2, t5, t3, t4);
                }
                tour.add_length(-gain);
                return -gain;
            }
        }
    }
    0
}
