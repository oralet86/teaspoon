//! O16 and O18: a Lin-Kernighan step built from 2-opt moves.

use super::{Application, Problem, Work, exchange, pick_anchor};
use crate::City;
use crate::instance::neighbours::CandidateLists;
use crate::instance::storage::Dist;
use crate::rng::Stream;
use crate::tour::{Layout, Tour};

/// Most 2-opt moves one step chains (Applegate, Cook and Rohe 2003).
pub const LK_DEPTH: usize = 25;

/// Alternatives tried at levels 1, 2, 3, 4 and deeper before backtracking.
pub const LK_BREADTH: [usize; 5] = [4, 3, 3, 2, 1];

/// O16 (neighbour lists) and O18 (alpha lists): an LK step from the anchor
/// (`aos.operators.lin_kernighan`).
///
/// Removes an edge (t1, t2) at the anchor and grows a sequential k-opt
/// move one flip at a time: at each level a new edge from the current end
/// to a candidate t3 must keep the partial gain positive, the edge (t3, t4)
/// that keeps one cycle is removed, and the tour would close with (t4, t1).
/// The first closing that improves the tour is committed; otherwise the
/// alternatives with the largest partial gain are explored, [`LK_BREADTH`]
/// per level, up to [`LK_DEPTH`] flips.  Added edges are never removed
/// again; unproductive flips are undone.  Both tour directions are tried.
pub(super) fn lin_kernighan<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
    lists: &CandidateLists,
) -> Application {
    let city = pick_anchor(tour.n(), rng, anchor);
    let delta = lk_step(tour, problem, lists, city, work);
    Application {
        delta,
        anchor: city,
    }
}

/// One LK step at `city` in both directions; returns the committed change
/// (negative) or zero with the tour unchanged.
pub(super) fn lk_step<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    lists: &CandidateLists,
    city: City,
    work: &mut Work,
) -> i64 {
    for forward in [true, false] {
        let t2 = tour.succ_in(city, forward);
        let gain0 = problem.d(work, city, t2);
        let mut search = Search {
            problem,
            lists,
            t1: city,
            added: Vec::with_capacity(LK_DEPTH),
            work,
        };
        let gain = search.extend(tour, t2, gain0, 1);
        if gain > 0 {
            tour.add_length(-gain);
            return -gain;
        }
    }
    0
}

/// The state of one LK search from `t1`.
struct Search<'p, 'w, D: Dist, const C: bool> {
    problem: &'p Problem<'p, D, C>,
    lists: &'p CandidateLists,
    t1: City,
    /// Edges added so far, as (smaller, larger).
    added: Vec<(City, City)>,
    work: &'w mut Work,
}

#[inline(always)]
fn edge(a: City, b: City) -> (City, City) {
    if a < b { (a, b) } else { (b, a) }
}

impl<D: Dist, const C: bool> Search<'_, '_, D, C> {
    /// Extends the chain from `last` (adjacent to t1); returns the gain of
    /// a committed improvement, or zero with everything below undone.
    fn extend<L: Layout>(
        &mut self,
        tour: &mut Tour<L>,
        last: City,
        gain: i64,
        level: usize,
    ) -> i64 {
        if level > LK_DEPTH {
            return 0;
        }
        let t1 = self.t1;
        let forward = tour.next(t1) == last;
        let succ_last = tour.next(last);
        let pred_last = tour.prev(last);
        let mut options: Vec<(i64, City, City)> = Vec::with_capacity(self.lists.k());
        for slot in 0..self.lists.k() {
            let (t3, d_last_t3) = self.problem.candidate(self.work, self.lists, last, slot);
            let partial = gain - d_last_t3;
            if partial <= 0 {
                break;
            }
            if t3 == t1
                || t3 == succ_last
                || t3 == pred_last
                || self.added.contains(&edge(last, t3))
            {
                continue;
            }
            // The flip removing (t1, last) and (t3, t4) keeps one cycle
            // only if t4 lies before t3 in the direction t1 -> last.
            let t4 = if forward {
                tour.prev(t3)
            } else {
                tour.next(t3)
            };
            if t4 == t1 || self.added.contains(&edge(t3, t4)) {
                continue;
            }
            let opened = partial + self.problem.d(self.work, t3, t4);
            let closed = opened - self.problem.d(self.work, t4, t1);
            if closed > 0 {
                exchange(tour, self.work, t1, last, t4, t3);
                return closed;
            }
            options.push((opened, t3, t4));
        }
        // Largest partial gain first; ties as Python's reverse tuple sort.
        options.sort_unstable_by(|a, b| b.cmp(a));
        let breadth = LK_BREADTH[level.min(LK_BREADTH.len()) - 1];
        for &(opened, t3, t4) in options.iter().take(breadth) {
            let mark = tour.mark();
            exchange(tour, self.work, t1, last, t4, t3);
            self.added.push(edge(last, t3));
            let result = self.extend(tour, t4, opened, level + 1);
            if result > 0 {
                return result;
            }
            self.added.pop();
            // Counted like Python's explicit inverse exchange.
            self.work.flips += 1;
            self.work.positions += tour.undo_to(mark);
        }
        0
    }
}
