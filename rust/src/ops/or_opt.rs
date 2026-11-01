//! O9 and the segment move it shares with O2 and O4.

use super::{Application, Problem, Work, exchange, pick_anchor};
use crate::City;
use crate::instance::storage::Dist;
use crate::rng::Stream;
use crate::tour::{Layout, Tour};

/// Longest segment O9 moves (the classic Or-opt limit).
pub const MAX_SEGMENT: usize = 3;

/// Moves the tour segment `first .. last` between the adjacent cities
/// `target` and `other`, with `first` next to `target`
/// (`aos.operators.insert_segment`).
///
/// `outside` and `after` are the segment's tour neighbours beyond `first`
/// and `last`; the edge (target, other) must not touch the segment,
/// `outside` or `after`.  Done as two or three 2-opt moves, so it works in
/// either direction of travel.  The length is not changed here.
#[allow(clippy::too_many_arguments)]
pub(super) fn insert_segment<L: Layout>(
    tour: &mut Tour<L>,
    work: &mut Work,
    outside: City,
    first: City,
    last: City,
    after: City,
    target: City,
    other: City,
    size: usize,
) {
    // Direction of travel from `first` towards `last`.
    let forward = tour.next(outside) == first;
    if tour.succ_in(target, forward) == other {
        // outside S after .. target other -> outside after .. target S other
        exchange(tour, work, outside, first, target, other);
        exchange(tour, work, outside, target, after, last);
        if size > 1 {
            exchange(tour, work, target, last, first, other);
        }
        return;
    }
    // outside S after .. other target -> outside after .. other S' target
    exchange(tour, work, outside, first, other, target);
    exchange(tour, work, outside, other, after, last);
}

/// O9: the first improving Or-opt move of a segment of 1-3 cities that
/// starts at the anchor (in either tour direction), reinserted in either
/// orientation so that the anchor sits next to one of its 10 nearest
/// neighbours.  The neighbour list is cut off at the first neighbour whose
/// new edge is at least as long as the gain from taking the segment out
/// (Johnson and McGeoch 1997).
pub(super) fn or_opt_nl<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let city = pick_anchor(tour.n(), rng, anchor);
    let lists = problem.neighbours;
    for forward in [true, false] {
        let outside = tour.succ_in(city, !forward);
        let mut segment = [city; MAX_SEGMENT];
        let mut last = city;
        for size in 1..=MAX_SEGMENT {
            if size > 1 {
                last = tour.succ_in(last, forward);
                segment[size - 1] = last;
            }
            let after = tour.succ_in(last, forward);
            let removal = problem.d(work, outside, city) + problem.d(work, last, after)
                - problem.d(work, outside, after);
            if removal <= 0 {
                continue;
            }
            for slot in 0..lists.k() {
                let (target, added) = problem.candidate(work, lists, city, slot);
                if added >= removal {
                    break;
                }
                if target == outside || target == after || segment[..size].contains(&target) {
                    continue;
                }
                for other in [
                    tour.succ_in(target, forward),
                    tour.succ_in(target, !forward),
                ] {
                    if other == outside || other == after {
                        continue;
                    }
                    let delta = added + problem.d(work, last, other)
                        - problem.d(work, target, other)
                        - removal;
                    if delta >= 0 {
                        continue;
                    }
                    insert_segment(tour, work, outside, city, last, after, target, other, size);
                    tour.add_length(delta);
                    return Application {
                        delta,
                        anchor: city,
                    };
                }
            }
        }
    }
    Application {
        delta: 0,
        anchor: city,
    }
}
