//! Instances: TSPLIB loading, distance storage, candidate lists and alpha
//! candidates (`ideas/rust-port.md` §4).

pub mod alpha;
pub mod distance;
pub mod neighbours;
pub mod storage;
pub mod tsplib;

use std::path::{Path, PathBuf};

use anyhow::{Context, bail};
use sha2::{Digest, Sha256};

use self::alpha::{AscentSettings, CacheKey};
use self::neighbours::{ALPHA_CANDIDATES, CandidateLists, NEIGHBOURS};
use self::storage::{Computed, Dist, Matrix};
use self::tsplib::Weights;
use crate::City;

/// One symmetric TSP instance in the main storage design: the full matrix,
/// optional coordinates for the `computed` mode, and candidate lists.
#[derive(Debug)]
pub struct Instance {
    /// The TSPLIB `NAME`.
    pub name: String,
    /// The file it was read from.
    pub path: PathBuf,
    /// SHA-256 of the file, hex.
    pub sha256: String,
    /// Dense distances (main design, option A).
    pub matrix: Matrix,
    /// Coordinates, when the instance has them.
    pub computed: Option<Computed>,
    /// The 10 nearest neighbours of every city.
    pub neighbours: CandidateLists,
    /// The 5 alpha-nearest candidates, when computed or cached.
    pub alpha: Option<CandidateLists>,
    /// Optimal or best-known length, when known.
    pub optimum: Option<i64>,
}

/// Hex SHA-256 of a byte string.
pub fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// Length of the closed tour visiting `order`.
pub fn tour_length(dist: &impl Dist, order: &[City]) -> i64 {
    let mut total = 0i64;
    let mut previous = *order.last().expect("a tour has cities");
    for &city in order {
        total += i64::from(dist.d(previous, city));
        previous = city;
    }
    total
}

/// Returns `name`'s length from a TSPLIB `solution.txt` list
/// (`name : length` lines), or `None` when the file or entry is missing.
///
/// # Errors
///
/// If the entry exists but is not a whole number.
fn listed_optimum(listing: &Path, name: &str) -> anyhow::Result<Option<i64>> {
    if !listing.is_file() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(listing)?;
    for line in text.lines() {
        let Some((key, value)) = line.split_once(':') else {
            continue;
        };
        if key.trim() == name {
            let token = value.split_whitespace().next().unwrap_or("");
            let length: i64 = token
                .parse()
                .with_context(|| format!("{}: {name} has length {token:?}", listing.display()))?;
            return Ok(Some(length));
        }
    }
    Ok(None)
}

/// The companion tour of an instance file (`<stem>.opt.tour`, then
/// `<stem>.tour`), as `aos.problem.Problem.from_tsplib` finds it.
fn companion_tour(path: &Path) -> Option<PathBuf> {
    let stem = path.file_stem()?.to_str()?;
    [".opt.tour", ".tour"]
        .iter()
        .map(|suffix| path.with_file_name(format!("{stem}{suffix}")))
        .find(|candidate| candidate.is_file())
}

impl Instance {
    /// Loads a TSPLIB file: the matrix, coordinates, the 10 nearest
    /// neighbours, the optimum, and cached alpha candidates (not computed
    /// here; see [`Instance::ensure_alpha`]).
    ///
    /// The optimum is the length of a companion tour if one exists, else
    /// the entry in `solution.txt` next to the file, else unknown.
    ///
    /// # Errors
    ///
    /// If the file is not a supported symmetric TSP, a distance overflows
    /// `i32`, or a companion tour or cache file is invalid.
    pub fn load(path: &Path, alpha_cache: &Path) -> anyhow::Result<Self> {
        let bytes = std::fs::read(path).with_context(|| format!("reading {}", path.display()))?;
        let sha256 = sha256_hex(&bytes);
        let file = tsplib::read(path)?;
        let n = file.dimension;
        let (matrix, computed) = match file.weights {
            Weights::Explicit(values) => (Matrix::new(n, values), None),
            Weights::Coordinates { kind, points } => {
                let computed = Computed::new(kind, points);
                let matrix = Matrix::from_computed(&computed)
                    .map_err(|message| anyhow::anyhow!("{}: {message}", path.display()))?;
                (matrix, Some(computed))
            }
        };
        let optimum = match companion_tour(path) {
            Some(tour_path) => Some(tour_length(&matrix, &tsplib::read_tour(&tour_path, n)?)),
            None => listed_optimum(&path.with_file_name("solution.txt"), &file.name)?,
        };
        let mut instance =
            Self::from_distances(file.name, path.to_path_buf(), sha256, matrix, computed);
        instance.optimum = optimum;
        let key = instance.alpha_key(AscentSettings::default());
        if let Some(lists) = alpha::load_cached(alpha_cache, &instance.name, &key)? {
            instance.attach_alpha(&lists)?;
        }
        Ok(instance)
    }

    /// An instance of the distances `matrix` (computed from `computed` when
    /// it has coordinates) with its nearest neighbours, no alpha candidates
    /// and no known optimum.
    pub fn from_distances(
        name: String,
        path: PathBuf,
        sha256: String,
        matrix: Matrix,
        computed: Option<Computed>,
    ) -> Self {
        let neighbours = CandidateLists::nearest(&matrix, NEIGHBOURS);
        Self {
            name,
            path,
            sha256,
            matrix,
            computed,
            neighbours,
            alpha: None,
            optimum: None,
        }
    }

    /// Number of cities.
    pub fn n(&self) -> usize {
        self.matrix.n()
    }

    /// The cache key of this instance's alpha candidates.
    pub fn alpha_key(&self, ascent: AscentSettings) -> CacheKey {
        CacheKey {
            instance_sha256: self.sha256.clone(),
            k: ALPHA_CANDIDATES,
            ascent,
            code_version: alpha::ALPHA_CODE_VERSION,
        }
    }

    fn attach_alpha(&mut self, lists: &[Vec<City>]) -> anyhow::Result<()> {
        let lists = CandidateLists::from_lists(lists, &self.matrix)
            .map_err(|message| anyhow::anyhow!("{}: alpha candidates: {message}", self.name))?;
        if lists.k() != ALPHA_CANDIDATES.min(self.n() - 1) {
            bail!(
                "{}: {} alpha candidates per city, expected {ALPHA_CANDIDATES}",
                self.name,
                lists.k()
            );
        }
        self.alpha = Some(lists);
        Ok(())
    }

    /// Attaches alpha candidates, computing and caching them when no
    /// matching cache exists.  Returns whether they were computed.
    ///
    /// # Errors
    ///
    /// If the cache cannot be written.
    pub fn ensure_alpha(&mut self, alpha_cache: &Path) -> anyhow::Result<bool> {
        if self.alpha.is_some() {
            return Ok(false);
        }
        let lists = self.compute_alpha()?;
        let key = self.alpha_key(AscentSettings::default());
        alpha::store(alpha_cache, &self.name, &key, &lists)?;
        Ok(true)
    }

    /// Computes and attaches alpha candidates (untimed; O(n²) per ascent
    /// iteration), without the cache; returns the lists.
    ///
    /// # Errors
    ///
    /// If the lists are inconsistent with the matrix.
    pub fn compute_alpha(&mut self) -> anyhow::Result<Vec<Vec<City>>> {
        let pi = alpha::ascend(&self.matrix, AscentSettings::default());
        let lists = alpha::alpha_candidates(&self.matrix, &pi, ALPHA_CANDIDATES);
        self.attach_alpha(&lists)?;
        Ok(lists)
    }
}
