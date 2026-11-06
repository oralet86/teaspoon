//! O5: 2-opt at the anchor with a full scan of the tour.
//!
//! O6 (the best improving move of the same scan) was retired on 2026-11-05.

use super::{Application, Problem, Work, pick_anchor, reverse};
use crate::City;
use crate::instance::storage::Dist;
use crate::rng::Stream;
use crate::tour::{Layout, Tour};

/// From the anchor edge (a, next(a)), scan every other edge (c, next(c))
/// along the tour and apply the first improving 2-opt move. Costs O(n)
/// distance reads when nothing improves.
pub(super) fn two_opt_first<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let n = tour.n();
    let a = pick_anchor(n, rng, anchor);
    let b = tour.next(a);
    let base = problem.d(work, a, b);
    let mut c = tour.next(b);
    for _ in 2..n - 1 {
        let d = tour.next(c);
        let delta = problem.d(work, a, c) + problem.d(work, b, d) - base - problem.d(work, c, d);
        if delta < 0 {
            // a b .. c d -> a c .. b d
            reverse(tour, work, b, c);
            tour.add_length(delta);
            return Application { delta, anchor: a };
        }
        c = d;
    }
    Application {
        delta: 0,
        anchor: a,
    }
}
