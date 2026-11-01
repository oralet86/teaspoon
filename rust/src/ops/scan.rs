//! O5 and O6: 2-opt at the anchor with a full scan of the tour.

use super::{Application, Problem, Work, pick_anchor, reverse};
use crate::City;
use crate::instance::storage::Dist;
use crate::rng::Stream;
use crate::tour::{Layout, Tour};

/// From the anchor edge (a, next(a)), scan every other edge (c, next(c))
/// along the tour and apply the first (O5) or the best (O6, `best`)
/// improving 2-opt move.  O5 costs O(n) when nothing improves; O6 always.
/// Ties keep the earliest move along the tour, as in Python.
pub(super) fn two_opt_scan<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
    best: bool,
) -> Application {
    let n = tour.n();
    let a = pick_anchor(n, rng, anchor);
    let b = tour.next(a);
    let base = problem.d(work, a, b);
    let mut best_delta = 0;
    let mut best_c = a;
    let mut c = tour.next(b);
    for _ in 2..n - 1 {
        let d = tour.next(c);
        let delta = problem.d(work, a, c) + problem.d(work, b, d) - base - problem.d(work, c, d);
        if delta < best_delta {
            best_delta = delta;
            best_c = c;
            if !best {
                break;
            }
        }
        c = d;
    }
    if best_delta == 0 {
        return Application {
            delta: 0,
            anchor: a,
        };
    }
    // a b .. c d -> a c .. b d
    reverse(tour, work, b, best_c);
    tour.add_length(best_delta);
    Application {
        delta: best_delta,
        anchor: a,
    }
}
