//! Distance storage (`ideas/rust-port.md` §4.2).
//!
//! The main design expands every instance into one dense row-major `i32`
//! matrix, so a read costs the same for every instance type at equal n.
//! The `computed` mode (coordinate instances only) evaluates the TSPLIB
//! function on every read; it exists for the robustness factors and gives
//! exactly the same distances.

use super::distance::{att, euc_2d, geo};
use super::tsplib::CoordinateKind;
use crate::City;

/// Read access to the distances of one instance.
///
/// Operators are generic over this trait, so each storage mode is
/// monomorphised and the hot path has no dynamic dispatch.
pub trait Dist: Sync {
    /// Number of cities.
    fn n(&self) -> usize;

    /// Distance between cities `i` and `j`.
    fn d(&self, i: City, j: City) -> i32;
}

/// A dense row-major `n × n` matrix.
#[derive(Debug, Clone)]
pub struct Matrix {
    n: usize,
    values: Vec<i32>,
}

impl Matrix {
    /// Wraps a row-major matrix.
    ///
    /// # Panics
    ///
    /// If `values` does not hold `n * n` entries (an internal invariant:
    /// callers build it from a parsed file).
    pub fn new(n: usize, values: Vec<i32>) -> Self {
        assert_eq!(values.len(), n * n, "matrix must hold n * n entries");
        Self { n, values }
    }

    /// Expands coordinates into a matrix, refusing distances that do not
    /// fit in `i32`.
    ///
    /// # Errors
    ///
    /// The first pair whose distance overflows `i32`.
    pub fn from_computed(computed: &Computed) -> Result<Self, String> {
        let n = computed.n();
        let mut values = Vec::with_capacity(n * n);
        for i in 0..n as City {
            for j in 0..n as City {
                let value = computed.exact(i, j);
                let value = i32::try_from(value)
                    .map_err(|_| format!("distance {value} between {i} and {j} overflows i32"))?;
                values.push(value);
            }
        }
        Ok(Self { n, values })
    }

    /// The row of city `i`.
    pub fn row(&self, i: City) -> &[i32] {
        let start = i as usize * self.n;
        &self.values[start..start + self.n]
    }
}

impl Dist for Matrix {
    #[inline(always)]
    fn n(&self) -> usize {
        self.n
    }

    #[inline(always)]
    fn d(&self, i: City, j: City) -> i32 {
        self.values[i as usize * self.n + j as usize]
    }
}

/// Distances computed from coordinates on every read.
#[derive(Debug, Clone)]
pub struct Computed {
    kind: CoordinateKind,
    points: Vec<[f64; 2]>,
}

impl Computed {
    /// Wraps coordinates (radians for [`CoordinateKind::Geo`]).
    pub fn new(kind: CoordinateKind, points: Vec<[f64; 2]>) -> Self {
        Self { kind, points }
    }

    /// The distance function.
    pub fn kind(&self) -> CoordinateKind {
        self.kind
    }

    /// The distance as computed, before narrowing to `i32`.
    fn exact(&self, i: City, j: City) -> i64 {
        let a = self.points[i as usize];
        let b = self.points[j as usize];
        match self.kind {
            CoordinateKind::Euc2d => euc_2d(a, b),
            CoordinateKind::Att => att(a, b),
            CoordinateKind::Geo => geo(a, b),
        }
    }
}

impl Dist for Computed {
    #[inline(always)]
    fn n(&self) -> usize {
        self.points.len()
    }

    #[inline(always)]
    fn d(&self, i: City, j: City) -> i32 {
        // Every pair was checked to fit when the instance was loaded
        // (`Matrix::from_computed`), so the narrowing is exact.
        self.exact(i, j) as i32
    }
}
