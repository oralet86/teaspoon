//! O20: neighbour-list segment exchange.

use super::{Application, Problem, Work, pick_anchor, reverse};
use crate::City;
use crate::instance::storage::Dist;
use crate::rng::Stream;
use crate::tour::{Layout, Tour};

/// O20: the first improving exchange of the segment of 1-3 cities that
/// starts at the anchor with a segment of 1-3 cities that starts at one of
/// the anchor's nearest neighbours, each placed in its better orientation
/// (the neighbour-list segment exchange of FILO and HGS-CVRP, within one
/// tour).  The two segments must not touch.  Deterministic given the
/// anchor.
pub(super) fn segment_exchange<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let n = tour.n();
    let a = pick_anchor(n, rng, anchor);
    let p = tour.prev(a);
    for size_a in 1..=3 {
        let a_last = tour.walk(a, size_a - 1);
        let q = tour.walk(a, size_a);
        let old_a = problem.d(work, p, a) + problem.d(work, a_last, q);
        for &b in problem.neighbours.cities(a) {
            let offset = tour.steps(a, b);
            for size_b in 1..=3 {
                if offset < size_a + 1 || offset + size_b > n - 1 {
                    continue;
                }
                let b_last = tour.walk(b, size_b - 1);
                let r = tour.prev(b);
                let s = tour.walk(b, size_b);
                let old = old_a + problem.d(work, r, b) + problem.d(work, b_last, s);
                let b_forward = problem.d(work, p, b) + problem.d(work, b_last, q);
                let b_backward = problem.d(work, p, b_last) + problem.d(work, b, q);
                let a_forward = problem.d(work, r, a) + problem.d(work, a_last, s);
                let a_backward = problem.d(work, r, a_last) + problem.d(work, a, s);
                let delta = b_forward.min(b_backward) + a_forward.min(a_backward) - old;
                if delta >= 0 {
                    continue;
                }
                // p [A] q .. r [B] s -> p [B' M' A'] s -> p [B' M A'] s,
                // then each segment turned to its better orientation.
                reverse(tour, work, a, b_last);
                reverse(tour, work, r, q);
                if b_backward >= b_forward {
                    reverse(tour, work, b_last, b);
                }
                if a_backward >= a_forward {
                    reverse(tour, work, a_last, a);
                }
                tour.add_length(delta);
                return Application { delta, anchor: a };
            }
        }
    }
    Application {
        delta: 0,
        anchor: a,
    }
}
