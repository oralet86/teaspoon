//! Alpha-nearness candidates (Helsgaun 2000), as used by LKH; a port of
//! `aos/candidates.py`.
//!
//! Node penalties pi are tuned by subgradient ascent on the Held-Karp
//! 1-tree bound, with transformed weights `d'(i, j) = d(i, j) + pi_i +
//! pi_j`.  The alpha value of an edge is how much the minimum 1-tree grows
//! when forced to contain it.  Each city's `k` alpha-nearest cities are
//! returned **ordered by distance**, so the operators' neighbour-list
//! stopping rule stays valid.
//!
//! Memory is O(n): transformed weights are computed on the fly, never
//! stored as a matrix, so rl11849 is feasible.  Results are cached on disk
//! under a key of instance digest, ascent settings and code version; a
//! cache whose key does not match is recomputed, never reused.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::storage::Dist;
use crate::City;

/// Bumped whenever the computation changes, which invalidates every cache.
pub const ALPHA_CODE_VERSION: u32 = 1;

/// Subgradient ascent settings (LKH-like defaults).
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct AscentSettings {
    /// Hard cap on ascent iterations (LKH has none; it keeps dense
    /// instances of 10,000+ cities affordable).
    pub max_iterations: u32,
    /// Initial step size T.
    pub initial_step: f64,
}

impl Default for AscentSettings {
    fn default() -> Self {
        Self {
            max_iterations: 300,
            initial_step: 1.0,
        }
    }
}

/// A minimum 1-tree.
struct OneTree {
    /// Tree parent of each city; `u32::MAX` for the root and the special city.
    parent: Vec<u32>,
    /// Prim's visiting order (root first, special city excluded).
    order: Vec<u32>,
    /// Total transformed weight.
    total: f64,
    /// Degree of every city in the 1-tree.
    degree: Vec<i64>,
}

const NONE: u32 = u32::MAX;

/// Transformed weight `d(i, j) + pi_i + pi_j`, evaluated in the same order
/// as `dist + pi[:, None] + pi[None, :]` in Python.
#[inline(always)]
fn weight(dist: &impl Dist, pi: &[f64], i: usize, j: usize) -> f64 {
    (f64::from(dist.d(i as City, j as City)) + pi[i]) + pi[j]
}

/// The special city of every 1-tree (as in LKH): left out of the spanning
/// tree and joined by its two cheapest edges.
const SPECIAL: usize = 0;

/// Minimum 1-tree: Prim's minimum spanning tree on every city but
/// [`SPECIAL`], plus the two cheapest edges at it.
fn minimum_one_tree(dist: &impl Dist, pi: &[f64]) -> OneTree {
    let n = dist.n();
    let root = SPECIAL + 1;
    let mut parent = vec![NONE; n];
    let mut in_tree = vec![false; n];
    in_tree[SPECIAL] = true;
    in_tree[root] = true;
    let mut best: Vec<f64> = (0..n)
        .map(|j| {
            if in_tree[j] {
                f64::INFINITY
            } else {
                weight(dist, pi, root, j)
            }
        })
        .collect();
    let mut best_from = vec![root as u32; n];
    let mut order = Vec::with_capacity(n - 1);
    order.push(root as u32);
    let mut total = 0.0;
    let mut degree = vec![0i64; n];
    for _ in 0..n - 2 {
        // First minimum, as numpy.argmin.
        let mut city = 0;
        let mut value = f64::INFINITY;
        for (index, &candidate) in best.iter().enumerate() {
            if candidate < value {
                value = candidate;
                city = index;
            }
        }
        total += value;
        let from = best_from[city] as usize;
        parent[city] = from as u32;
        degree[city] += 1;
        degree[from] += 1;
        in_tree[city] = true;
        order.push(city as u32);
        best[city] = f64::INFINITY;
        for j in 0..n {
            if !in_tree[j] {
                let w = weight(dist, pi, city, j);
                if w < best[j] {
                    best[j] = w;
                    best_from[j] = city as u32;
                }
            }
        }
    }
    let (first, second) = two_smallest(dist, pi, SPECIAL);
    total += first.1 + second.1;
    degree[SPECIAL] = 2;
    degree[first.0] += 1;
    degree[second.0] += 1;
    OneTree {
        parent,
        order,
        total,
        degree,
    }
}

/// The two cheapest transformed edges at `city` as (other city, weight),
/// cheapest first; ties go to the lower index.
fn two_smallest(dist: &impl Dist, pi: &[f64], city: usize) -> ((usize, f64), (usize, f64)) {
    let mut first = (NONE as usize, f64::INFINITY);
    let mut second = (NONE as usize, f64::INFINITY);
    for j in 0..dist.n() {
        if j == city {
            continue;
        }
        let w = weight(dist, pi, city, j);
        if w < first.1 {
            second = first;
            first = (j, w);
        } else if w < second.1 {
            second = (j, w);
        }
    }
    (first, second)
}

/// Subgradient ascent on the 1-tree bound; returns the penalties of the
/// best bound found (Helsgaun 2000; LKH `Ascent.c`; the rules of
/// `aos.candidates.ascend`).
pub fn ascend(dist: &impl Dist, settings: AscentSettings) -> Vec<f64> {
    let n = dist.n();
    let mut pi = vec![0.0; n];
    let mut best_pi = pi.clone();
    let mut best_bound = f64::NEG_INFINITY;
    let mut step = settings.initial_step;
    let initial_period = (n / 2).max(100);
    let mut period = initial_period;
    let mut previous = vec![0.0; n];
    let mut initial = true;
    let mut iteration = 0;
    while step > 1e-9 && period > 0 {
        let mut since_improvement = 0;
        let mut position = 0;
        while position < period {
            position += 1;
            iteration += 1;
            let tree = minimum_one_tree(dist, &pi);
            let bound = tree.total - 2.0 * pi.iter().sum::<f64>();
            let v: Vec<f64> = tree.degree.iter().map(|&d| (d - 2) as f64).collect();
            if bound > best_bound + 1e-9 {
                best_bound = bound;
                best_pi.clone_from(&pi);
                since_improvement = 0;
                if initial {
                    step *= 2.0;
                }
                if position == period {
                    period = (2 * period).min(initial_period);
                }
            } else {
                since_improvement += 1;
                if initial && since_improvement > period / 2 {
                    initial = false;
                    step *= 0.75;
                    position = 0;
                }
            }
            if v.iter().all(|&value| value == 0.0) || iteration >= settings.max_iterations {
                return best_pi;
            }
            for ((p, &value), prev) in pi.iter_mut().zip(&v).zip(&mut previous) {
                *p += step * (0.7 * value + 0.3 * *prev);
                *prev = value;
            }
        }
        step /= 2.0;
        period /= 2;
    }
    best_pi
}

/// The `k` cities with the smallest alpha (ties by distance, then index),
/// ordered by distance (ties keep the alpha order).
fn best_k(alpha: &[f64], distances: &[i32], k: usize) -> Vec<City> {
    let mut ranked: Vec<usize> = (0..alpha.len()).collect();
    ranked.sort_by(|&a, &b| {
        alpha[a]
            .total_cmp(&alpha[b])
            .then(distances[a].cmp(&distances[b]))
            .then(a.cmp(&b))
    });
    ranked.truncate(k);
    ranked.sort_by_key(|&city| distances[city]);
    ranked.into_iter().map(|city| city as City).collect()
}

/// Every city's `k` alpha-nearest cities, ordered by distance (the rules
/// of `aos.candidates.alpha_candidates`).
pub fn alpha_candidates(dist: &impl Dist, pi: &[f64], k: usize) -> Vec<Vec<City>> {
    let n = dist.n();
    let tree = minimum_one_tree(dist, pi);
    let root = tree.order[0] as usize;
    let edge_weight: Vec<f64> = (0..n)
        .map(|city| match tree.parent[city] {
            NONE => 0.0,
            parent => weight(dist, pi, city, parent as usize),
        })
        .collect();
    let second_at_special = two_smallest(dist, pi, SPECIAL).1.1;
    let mut candidates = vec![Vec::new(); n];
    let mut beta = vec![0.0; n];
    let mut mark = vec![NONE as usize; n];
    let mut alpha = vec![0.0; n];
    let mut row_distances = vec![0i32; n];
    for &i in &tree.order {
        let i = i as usize;
        beta[i] = f64::NEG_INFINITY;
        let mut city = i;
        while city != root {
            let up = tree.parent[city] as usize;
            beta[up] = beta[city].max(edge_weight[city]);
            mark[up] = i;
            city = up;
        }
        for &j in &tree.order[1..] {
            let j = j as usize;
            if j != i && mark[j] != i {
                beta[j] = beta[tree.parent[j] as usize].max(edge_weight[j]);
            }
        }
        for j in 0..n {
            alpha[j] = weight(dist, pi, i, j) - beta[j];
            row_distances[j] = dist.d(i as City, j as City);
        }
        alpha[i] = f64::INFINITY;
        alpha[SPECIAL] = (weight(dist, pi, i, SPECIAL) - second_at_special).max(0.0);
        candidates[i] = best_k(&alpha, &row_distances, k);
    }
    for j in 0..n {
        alpha[j] = (weight(dist, pi, SPECIAL, j) - second_at_special).max(0.0);
        row_distances[j] = dist.d(SPECIAL as City, j as City);
    }
    alpha[SPECIAL] = f64::INFINITY;
    candidates[SPECIAL] = best_k(&alpha, &row_distances, k);
    candidates
}

/// The key a cached candidate file must match to be reused.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CacheKey {
    /// SHA-256 of the instance file.
    pub instance_sha256: String,
    /// Candidates per city.
    pub k: usize,
    /// Ascent settings.
    pub ascent: AscentSettings,
    /// [`ALPHA_CODE_VERSION`].
    pub code_version: u32,
}

#[derive(Serialize, Deserialize)]
struct CacheFile {
    key: CacheKey,
    lists: Vec<Vec<City>>,
}

/// Directory of cached alpha candidates, relative to the repository root.
pub const CACHE_DIRECTORY: &str = "data/cache/candidates-rs";

/// The cache file of instance `name`.
fn cache_path(cache: &Path, name: &str, k: usize) -> PathBuf {
    cache.join(format!("{name}-alpha{k}.json"))
}

/// Returns the cached lists of `name` if the cache exists and its key
/// matches; `Ok(None)` when absent or stale (a stale cache is logged).
///
/// # Errors
///
/// If the file exists but cannot be read or parsed.
pub fn load_cached(
    cache: &Path,
    name: &str,
    key: &CacheKey,
) -> anyhow::Result<Option<Vec<Vec<City>>>> {
    let path = cache_path(cache, name, key.k);
    if !path.is_file() {
        return Ok(None);
    }
    let file: CacheFile = serde_json::from_str(&std::fs::read_to_string(&path)?)
        .map_err(|error| anyhow::anyhow!("{}: {error}", path.display()))?;
    if &file.key != key {
        log::warn!(
            "{}: cache key {:?} does not match {:?}; recomputing",
            path.display(),
            file.key,
            key
        );
        return Ok(None);
    }
    Ok(Some(file.lists))
}

/// Writes lists to the cache (atomically: temporary file, then rename).
///
/// # Errors
///
/// If the directory or file cannot be written.
pub fn store(cache: &Path, name: &str, key: &CacheKey, lists: &[Vec<City>]) -> anyhow::Result<()> {
    std::fs::create_dir_all(cache)?;
    let path = cache_path(cache, name, key.k);
    let temporary = path.with_extension("json.tmp");
    let file = CacheFile {
        key: key.clone(),
        lists: lists.to_vec(),
    };
    std::fs::write(&temporary, serde_json::to_string(&file)?)?;
    std::fs::rename(&temporary, &path)?;
    Ok(())
}
