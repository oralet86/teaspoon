//! O1-O4: random moves at the anchor, applied only if they improve.
//!
//! Python draws random array positions; here random cities and offsets
//! along the tour are drawn with the same distributions.

use super::or_opt::insert_segment;
use super::{Application, Problem, Work, pick_anchor, reverse};
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

/// O2: move the anchor between two other adjacent cities, chosen at
/// random, if shorter.
pub(super) fn insertion<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let n = tour.n();
    let city = pick_anchor(n, rng, anchor);
    let before = tour.prev(city);
    let after = tour.next(city);
    // Target edge (left, right): any edge but the two at the city.
    let left = tour.walk(city, 1 + rng.index(n - 2));
    let right = tour.next(left);
    let removal = problem.d(work, before, city) + problem.d(work, city, after)
        - problem.d(work, before, after);
    let addition =
        problem.d(work, left, city) + problem.d(work, city, right) - problem.d(work, left, right);
    let delta = addition - removal;
    if delta >= 0 {
        return Application {
            delta: 0,
            anchor: city,
        };
    }
    if left == after {
        // before city after right -> before after city right
        reverse(tour, work, city, after);
    } else if right == before {
        // left before city after -> left city before after
        reverse(tour, work, before, city);
    } else {
        insert_segment(tour, work, before, city, city, after, left, right, 1);
    }
    tour.add_length(delta);
    Application {
        delta,
        anchor: city,
    }
}

/// O3: reverse a segment of random length (2 to n - 2 cities) starting at
/// the anchor (a random 2-opt move) if that shortens the tour.
pub(super) fn inversion<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let n = tour.n();
    let first = pick_anchor(n, rng, anchor);
    let length = 2 + rng.index(n - 3);
    let last = tour.walk(first, length - 1);
    let before = tour.prev(first);
    let after = tour.next(last);
    let delta = problem.d(work, before, last) + problem.d(work, first, after)
        - problem.d(work, before, first)
        - problem.d(work, last, after);
    if delta >= 0 {
        return Application {
            delta: 0,
            anchor: first,
        };
    }
    reverse(tour, work, first, last);
    tour.add_length(delta);
    Application {
        delta,
        anchor: first,
    }
}

/// O4: move the segment of 1-3 cities (size random) that starts at the
/// anchor to a random edge not touching it or its two tour neighbours, in
/// its better orientation, if that shortens the tour.
pub(super) fn or_opt<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let n = tour.n();
    let first = pick_anchor(n, rng, anchor);
    let size = 1 + rng.index(3);
    let last = tour.walk(first, size - 1);
    let outside = tour.prev(first);
    let after = tour.walk(first, size);
    let left = tour.walk(first, size + 1 + rng.index(n - size - 3));
    let right = tour.next(left);
    let removal = problem.d(work, outside, first) + problem.d(work, last, after)
        - problem.d(work, outside, after);
    let forward =
        problem.d(work, left, first) + problem.d(work, last, right) - problem.d(work, left, right);
    let backward =
        problem.d(work, left, last) + problem.d(work, first, right) - problem.d(work, left, right);
    let reversed = backward < forward;
    let delta = (if reversed { backward } else { forward }) - removal;
    if delta >= 0 {
        return Application {
            delta: 0,
            anchor: first,
        };
    }
    if reversed {
        // left, last .. first, right: `first` sits next to `right`.
        insert_segment(tour, work, outside, first, last, after, right, left, size);
    } else {
        insert_segment(tour, work, outside, first, last, after, left, right, size);
    }
    tour.add_length(delta);
    Application {
        delta,
        anchor: first,
    }
}
