//! O11 and O19: remove cities and reinsert them greedily.
//!
//! The trial runs in an overlay of successor and predecessor links, so a
//! rejected trial costs O(removed · k) and leaves the tour untouched.  An
//! accepted result replaces the tour's order (O(n), as Python's rebuild;
//! logged, so it can be undone).

use super::{Application, Problem, Work, pick_anchor};
use crate::City;
use crate::instance::storage::Dist;
use crate::rng::Stream;
use crate::tour::{Layout, Tour};

/// Cities removed by O11: a random city and its nearest neighbours.
pub const RUIN_SIZE: usize = 10;

/// Longest string O19 removes (SISR's L_max).
pub const STRING_MAX: usize = 10;

/// SISR's k_s^max = 4 c / (1 + L_max) - 1 with c = 10: O19 removes
/// floor(U(1, k_s^max + 1)) strings, i.e. 1 to 3.
pub const STRING_COUNT: f64 = 4.0 * 10.0 / (1.0 + STRING_MAX as f64) - 1.0;

/// SISR's blink rate: each candidate position is skipped with this
/// probability.
pub const STRING_BLINK: f64 = 0.01;

/// Successor and predecessor links over the tour, with a few overrides.
struct Overlay<'t, L: Layout> {
    tour: &'t Tour<L>,
    successor: Vec<(City, City)>,
    predecessor: Vec<(City, City)>,
}

impl<'t, L: Layout> Overlay<'t, L> {
    fn new(tour: &'t Tour<L>) -> Self {
        Self {
            tour,
            successor: Vec::new(),
            predecessor: Vec::new(),
        }
    }

    fn get(links: &[(City, City)], city: City) -> Option<City> {
        links
            .iter()
            .rev()
            .find(|(key, _)| *key == city)
            .map(|&(_, value)| value)
    }

    fn succ(&self, city: City) -> City {
        Self::get(&self.successor, city).unwrap_or_else(|| self.tour.next(city))
    }

    fn pred(&self, city: City) -> City {
        Self::get(&self.predecessor, city).unwrap_or_else(|| self.tour.prev(city))
    }

    fn link(&mut self, left: City, right: City) {
        self.successor.push((left, right));
        self.predecessor.push((right, left));
    }
}

/// Removes `ruined` and reinserts them in random order, each at the
/// cheapest edge next to one of its neighbour-list cities that is in the
/// tour (all edges if none is), skipping each candidate edge with
/// probability `blink` (`aos.operators._ruin_and_recreate`).  Keeps the
/// result only if shorter; returns the change (negative) or zero.
fn ruin_and_recreate<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    ruined: &mut [City],
    blink: f64,
) -> i64 {
    let n = tour.n();
    let mut overlay = Overlay::new(tour);
    let mut removed: Vec<City> = Vec::with_capacity(ruined.len());
    let mut delta = 0;
    for &city in ruined.iter() {
        let before = overlay.pred(city);
        let after = overlay.succ(city);
        overlay.link(before, after);
        delta += problem.d(work, before, after)
            - problem.d(work, before, city)
            - problem.d(work, city, after);
        removed.push(city);
    }
    rng.shuffle(ruined);
    for &city in ruined.iter() {
        let mut best_cost = i64::MAX;
        let mut best_left = None;
        for &other in problem.neighbours.cities(city) {
            if removed.contains(&other) {
                continue;
            }
            for (left, right) in [(other, overlay.succ(other)), (overlay.pred(other), other)] {
                if blink > 0.0 && rng.unit() < blink {
                    continue;
                }
                let cost = problem.d(work, city, left) + problem.d(work, city, right)
                    - problem.d(work, left, right);
                if cost < best_cost {
                    best_cost = cost;
                    best_left = Some(left);
                }
            }
        }
        let best_left = match best_left {
            Some(left) => left,
            None => {
                // Every edge of the partial tour, from the first city (along
                // the tour from city 0) still in it.
                let mut left = 0;
                while removed.contains(&left) {
                    left = overlay.tour.next(left);
                }
                let mut chosen = left;
                for _ in 0..n - removed.len() {
                    let right = overlay.succ(left);
                    let cost = problem.d(work, city, left) + problem.d(work, city, right)
                        - problem.d(work, left, right);
                    if cost < best_cost {
                        best_cost = cost;
                        chosen = left;
                    }
                    left = right;
                }
                chosen
            }
        };
        let right = overlay.succ(best_left);
        overlay.link(best_left, city);
        overlay.link(city, right);
        removed.retain(|&other| other != city);
        delta += best_cost;
    }
    if delta >= 0 {
        return 0;
    }
    let start = (0..n as City)
        .find(|city| !ruined.contains(city))
        .expect("fewer cities are ruined than exist");
    let mut order = Vec::with_capacity(n);
    let mut city = start;
    for _ in 0..n {
        order.push(city);
        city = overlay.succ(city);
    }
    debug_assert_eq!(city, start, "the overlay is one cycle");
    work.positions += tour.replace(&order);
    tour.add_length(delta);
    delta
}

/// O11: radial ruin and greedy recreate (Schrimpf et al. 2000): removes
/// the anchor and its nearest neighbours (up to [`RUIN_SIZE`] cities).
pub(super) fn ruin_recreate<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let n = tour.n();
    let centre = pick_anchor(n, rng, anchor);
    let neighbours = problem.neighbours.cities(centre);
    let size = RUIN_SIZE.min(neighbours.len() + 1).min(n / 2);
    let mut ruined: Vec<City> = std::iter::once(centre)
        .chain(neighbours[..size - 1].iter().copied())
        .collect();
    let delta = ruin_and_recreate(tour, problem, rng, work, &mut ruined, 0.0);
    Application {
        delta,
        anchor: centre,
    }
}

/// O19: SISR-style string removal and blink recreate (Christiaens and
/// Vanden Berghe 2020), adapted to one tour: 1-3 strings of 1-10 cities,
/// the first containing the anchor, the others the next of its nearest
/// neighbours not yet removed, each at a random offset.
pub(super) fn string_removal<D: Dist, const C: bool, L: Layout>(
    tour: &mut Tour<L>,
    problem: &Problem<'_, D, C>,
    rng: &mut Stream,
    work: &mut Work,
    anchor: Option<City>,
) -> Application {
    let n = tour.n();
    let seed = pick_anchor(n, rng, anchor);
    let mut strings = (1.0 + rng.unit() * STRING_COUNT) as usize;
    let limit = STRING_MAX.min(n / 8).max(1);
    let mut ruined: Vec<City> = Vec::new();
    let mut cities: Vec<City> = Vec::with_capacity(limit);
    for &city in std::iter::once(&seed).chain(problem.neighbours.cities(seed)) {
        if strings == 0 {
            break;
        }
        if ruined.contains(&city) {
            continue;
        }
        let length = (1.0 + rng.unit() * limit as f64) as usize;
        let back = rng.index(length);
        let first = tour.walk(city, n - back);
        cities.clear();
        cities.extend((0..length).map(|offset| tour.walk(first, offset)));
        if cities.iter().any(|candidate| ruined.contains(candidate)) {
            continue;
        }
        ruined.extend_from_slice(&cities);
        strings -= 1;
    }
    let delta = ruin_and_recreate(tour, problem, rng, work, &mut ruined, STRING_BLINK);
    Application {
        delta,
        anchor: seed,
    }
}
