//! Candidate lists with their distances stored alongside
//! (`ideas/rust-port.md` §4.2, option C, and §4.3).

use super::storage::Dist;
use crate::City;

/// Number of nearest neighbours per city (`aos.problem.DEFAULT_NEIGHBOURS`).
pub const NEIGHBOURS: usize = 10;

/// Number of alpha-nearness candidates per city.
pub const ALPHA_CANDIDATES: usize = 5;

/// `k` candidates per city, ordered by increasing distance, with the
/// distance to each candidate stored contiguously next to it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CandidateLists {
    k: usize,
    cities: Vec<City>,
    distances: Vec<i32>,
}

impl CandidateLists {
    /// Builds lists from one candidate list per city, reading distances
    /// from `dist`.
    ///
    /// # Errors
    ///
    /// If the lists differ in length, are not sorted by distance, name the
    /// city itself or a city out of range.
    pub fn from_lists(lists: &[Vec<City>], dist: &impl Dist) -> Result<Self, String> {
        let n = dist.n();
        if lists.len() != n {
            return Err(format!("{} candidate lists for {n} cities", lists.len()));
        }
        let k = lists.first().map_or(0, Vec::len);
        let mut cities = Vec::with_capacity(n * k);
        let mut distances = Vec::with_capacity(n * k);
        for (city, list) in lists.iter().enumerate() {
            let city = city as City;
            if list.len() != k {
                return Err(format!(
                    "city {city} has {} candidates, expected {k}",
                    list.len()
                ));
            }
            let mut previous = i32::MIN;
            for &candidate in list {
                if candidate as usize >= n || candidate == city {
                    return Err(format!("city {city}: invalid candidate {candidate}"));
                }
                let distance = dist.d(city, candidate);
                if distance < previous {
                    return Err(format!("city {city}: candidates not ordered by distance"));
                }
                previous = distance;
                cities.push(candidate);
                distances.push(distance);
            }
        }
        Ok(Self {
            k,
            cities,
            distances,
        })
    }

    /// The `k` nearest other cities of every city, ties broken by index (a
    /// stable sort, as `aos.problem.Problem.from_matrix`).
    pub fn nearest(dist: &impl Dist, k: usize) -> Self {
        let n = dist.n();
        let k = k.min(n - 1);
        let lists: Vec<Vec<City>> = (0..n as City)
            .map(|city| {
                let mut others: Vec<(i32, City)> = (0..n as City)
                    .filter(|&other| other != city)
                    .map(|other| (dist.d(city, other), other))
                    .collect();
                others.select_nth_unstable(k - 1);
                others.truncate(k);
                others.sort_unstable();
                others.into_iter().map(|(_, other)| other).collect()
            })
            .collect();
        Self::from_lists(&lists, dist).expect("nearest-neighbour lists are valid by construction")
    }

    /// Candidates per city.
    #[inline(always)]
    pub fn k(&self) -> usize {
        self.k
    }

    /// The candidates of `city`, nearest first.
    #[inline(always)]
    pub fn cities(&self, city: City) -> &[City] {
        let start = city as usize * self.k;
        &self.cities[start..start + self.k]
    }

    /// The distances from `city` to its candidates, in list order.
    #[inline(always)]
    pub fn distances(&self, city: City) -> &[i32] {
        let start = city as usize * self.k;
        &self.distances[start..start + self.k]
    }

    /// Every list as plain vectors (for files and comparisons).
    pub fn to_lists(&self) -> Vec<Vec<City>> {
        self.cities
            .chunks(self.k.max(1))
            .map(<[City]>::to_vec)
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::instance::storage::Matrix;

    #[test]
    fn nearest_breaks_ties_by_index() {
        // City 0 is at distance 5 from 1, 2 and 3, and 1 from 4.
        let n = 5;
        let mut values = vec![9; n * n];
        for i in 0..n {
            values[i * n + i] = 0;
        }
        for j in 1..4 {
            values[j] = 5;
            values[j * n] = 5;
        }
        values[4] = 1;
        values[4 * n] = 1;
        let lists = CandidateLists::nearest(&Matrix::new(n, values), 3);
        assert_eq!(lists.cities(0), &[4, 1, 2]);
        assert_eq!(lists.distances(0), &[1, 5, 5]);
    }
}
