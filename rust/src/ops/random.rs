//! O1: a random move at the anchor, applied only if it improves.
//!
//! O2-O4 (random insertion, inversion and Or-opt) were retired on
//! 2026-11-05.
//!
//! Python draws random array positions; here random cities and offsets
//! along the tour are drawn with the same distributions.

use super::{Application, Problem, Work, pick_anchor};
use crate::City;
use crate::instance::storage::Dist;
use crate::rng::Stream;
use crate::tour::{Layout, Tour};

/// O1: exchange the anchor with a random other city if that shortens the
/// tour.
pub(super) fn swap<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let n = tour.n();
    let a = pick_anchor(n, rng, anchor);
    let b = tour.walk(a, 1 + rng.index(n - 1));
    let (pa, na, pb, nb) = (tour.prev(a), tour.next(a), tour.prev(b), tour.next(b));
    let delta = if na == b {
        // pa a b nb -> pa b a nb
        problem.d(work, pa, b) + problem.d(work, a, nb)
            - problem.d(work, pa, a)
            - problem.d(work, b, nb)
    } else if nb == a {
        // pb b a na -> pb a b na
        problem.d(work, pb, a) + problem.d(work, b, na)
            - problem.d(work, pb, b)
            - problem.d(work, a, na)
    } else {
        problem.d(work, pa, b)
            + problem.d(work, b, na)
            + problem.d(work, pb, a)
            + problem.d(work, a, nb)
            - problem.d(work, pa, a)
            - problem.d(work, a, na)
            - problem.d(work, pb, b)
            - problem.d(work, b, nb)
    };
    if delta < 0 {
        work.positions += tour.swap(a, b);
        tour.add_length(delta);
        return Application { delta, anchor: a };
    }
    Application {
        delta: 0,
        anchor: a,
    }
}
