//! Neighbour-list 2-opt: O7, O14, O15, O17 and the descent inside the
//! kicks.

use std::collections::VecDeque;

use super::{Application, Problem, Work, pick_anchor, reverse};
use crate::City;
use crate::instance::neighbours::CandidateLists;
use crate::instance::storage::Dist;
use crate::rng::Stream;
use crate::tour::{Layout, Tour};

/// Cities O15 compares before choosing where to apply O7.
pub const TOURNAMENT: usize = 8;

/// Applies the first improving 2-opt move that removes an edge at `city`,
/// searching `lists` (sorted by distance) in both tour directions,
/// successor first (`aos.operators.improve_city`).
///
/// A new edge from `city` must be shorter than the removed one, so the
/// search along the list stops at the first candidate that is not
/// (Johnson and McGeoch 1997).
///
/// Returns the length change (zero if no move was found) and the four
/// cities whose tour neighbours changed.
pub fn improve_city<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    lists: &CandidateLists,
    city: City,
    work: &mut Work,
) -> (i64, [City; 4]) {
    for successor in [true, false] {
        let partner = tour.succ_in(city, successor);
        let removed = problem.d(work, city, partner);
        for slot in 0..lists.k() {
            let (candidate, added) = problem.candidate(work, lists, city, slot);
            if added >= removed {
                break;
            }
            let other = tour.succ_in(candidate, successor);
            if candidate == partner || other == city {
                continue;
            }
            let delta = added + problem.d(work, partner, other)
                - removed
                - problem.d(work, candidate, other);
            if delta < 0 {
                if successor {
                    // city partner .. candidate other -> city candidate .. partner other
                    reverse(tour, work, partner, candidate);
                } else {
                    // partner city .. other candidate -> partner other .. city candidate
                    reverse(tour, work, city, other);
                }
                tour.add_length(delta);
                return (delta, [city, partner, candidate, other]);
            }
        }
    }
    (0, [city; 4])
}

/// O7 (with the neighbour lists) and O17 (with the alpha lists): the first
/// improving 2-opt move at the anchor over its candidate list.
pub(super) fn two_opt_nl<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
    lists: &CandidateLists,
) -> Application {
    let city = pick_anchor(tour.n(), rng, anchor);
    let (delta, _) = improve_city(tour, problem, lists, city, work);
    Application {
        delta,
        anchor: city,
    }
}

/// Applies neighbour-list 2-opt moves until none improves (an O7 local
/// optimum), processing cities from a queue (`aos.operators.descend`).
///
/// Cities whose search fails leave the queue; cities touched by an applied
/// move re-enter it (don't-look bits, Bentley 1992).  With `stop_gain`, the
/// descent stops as soon as its total gain reaches that value (used to
/// build partly descended tours).  Returns the total length change.
pub fn descend<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    work: &mut Work,
    start: &[City],
    stop_gain: Option<i64>,
) -> i64 {
    let mut queue: VecDeque<City> = VecDeque::with_capacity(start.len().max(16));
    let mut queued = vec![false; tour.n()];
    for &city in start {
        if !std::mem::replace(&mut queued[city as usize], true) {
            queue.push_back(city);
        }
    }
    let mut total = 0;
    while let Some(city) = queue.pop_front() {
        queued[city as usize] = false;
        let (delta, touched) = improve_city(tour, problem, problem.neighbours, city, work);
        if delta < 0 {
            total += delta;
            if stop_gain.is_some_and(|gain| -total >= gain) {
                break;
            }
            for other in touched {
                if !std::mem::replace(&mut queued[other as usize], true) {
                    queue.push_back(other);
                }
            }
        }
    }
    total
}

/// O14: an O7 descent started at the anchor.  Deterministic given the
/// anchor; one application can make many moves.
pub(super) fn two_opt_descent<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let city = pick_anchor(tour.n(), rng, anchor);
    let delta = descend(tour, problem, work, &[city], None);
    Application {
        delta,
        anchor: city,
    }
}

/// O15: O7 at the city with the worst local edge among the anchor and
/// [`TOURNAMENT`] - 1 random cities (drawn with replacement).  A city's
/// score is its longer tour edge minus the distance to its nearest
/// neighbour.  The returned anchor is the given (or drawn) one.
pub(super) fn two_opt_targeted<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let n = tour.n();
    let city = pick_anchor(n, rng, anchor);
    let mut best_city = city;
    let mut best_score = i64::MIN;
    for round in 0..TOURNAMENT {
        let candidate = if round == 0 {
            city
        } else {
            rng.index(n) as City
        };
        let longer = problem
            .d(work, candidate, tour.next(candidate))
            .max(problem.d(work, candidate, tour.prev(candidate)));
        let (_, nearest) = problem.candidate(work, problem.neighbours, candidate, 0);
        let score = longer - nearest;
        if score > best_score {
            best_score = score;
            best_city = candidate;
        }
    }
    let (delta, _) = improve_city(tour, problem, problem.neighbours, best_city, work);
    Application {
        delta,
        anchor: city,
    }
}
