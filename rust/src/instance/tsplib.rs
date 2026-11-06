//! TSPLIB reader for the symmetric TSP variants the experiments use.
//!
//! Supported (`ideas/rust-port.md` §4.1): `EUC_2D`, `ATT` and `GEO`
//! coordinates, and `EXPLICIT` weights in `FULL_MATRIX`, `UPPER_ROW`,
//! `UPPER_DIAG_ROW` and `LOWER_DIAG_ROW` layout.  Every other type or layout
//! is rejected with an error naming it, never approximated.

use std::path::{Path, PathBuf};

use thiserror::Error;

/// A TSPLIB file that cannot be used.
#[derive(Debug, Error)]
pub enum TsplibError {
    /// The file could not be read.
    #[error("{path}: {source}")]
    Io {
        /// The file.
        path: PathBuf,
        /// The underlying error.
        source: std::io::Error,
    },
    /// The file is malformed or uses an unsupported variant.
    #[error("{path}: {message}")]
    Format {
        /// The file.
        path: PathBuf,
        /// What is wrong.
        message: String,
    },
}

/// How distances are defined by coordinates.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum CoordinateKind {
    /// Rounded Euclidean distance.
    Euc2d,
    /// TSPLIB pseudo-Euclidean distance.
    Att,
    /// Great-circle distance; coordinates are stored in radians.
    Geo,
}

/// The weights of a parsed instance.
#[derive(Debug, Clone)]
pub enum Weights {
    /// Distances follow from coordinates.  For [`CoordinateKind::Geo`] the
    /// coordinates are (latitude, longitude) in radians.
    Coordinates {
        /// The distance function.
        kind: CoordinateKind,
        /// One point per city.
        points: Vec<[f64; 2]>,
    },
    /// A full, symmetric `n × n` matrix, row-major.
    Explicit(Vec<i32>),
}

/// A parsed TSPLIB instance.
#[derive(Debug, Clone)]
pub struct TsplibFile {
    /// The `NAME` field.
    pub name: String,
    /// Number of cities.
    pub dimension: usize,
    /// The distance definition.
    pub weights: Weights,
}

/// Header keywords that open a data section.
const SECTIONS: &[&str] = &[
    "NODE_COORD_SECTION",
    "DISPLAY_DATA_SECTION",
    "EDGE_WEIGHT_SECTION",
    "DEMAND_SECTION",
    "DEPOT_SECTION",
    "FIXED_EDGES_SECTION",
    "EDGE_DATA_SECTION",
    "TOUR_SECTION",
    "EOF",
];

fn format_error(path: &Path, message: impl Into<String>) -> TsplibError {
    TsplibError::Format {
        path: path.to_path_buf(),
        message: message.into(),
    }
}

/// Header fields and named sections of a TSPLIB file.
type Parts = (Vec<(String, String)>, Vec<(String, Vec<String>)>);

/// Splits TSPLIB text into header fields (keys upper-cased) and sections.
fn split(text: &str, path: &Path) -> Result<Parts, TsplibError> {
    let lines: Vec<&str> = text.lines().map(str::trim).collect();
    let mut header = Vec::new();
    let mut index = 0;
    while index < lines.len() {
        let line = lines[index];
        if line.is_empty() {
            index += 1;
            continue;
        }
        let keyword = line.split_whitespace().next().unwrap_or("").to_uppercase();
        if SECTIONS.contains(&keyword.as_str()) {
            break;
        }
        let Some((key, value)) = line.split_once(':') else {
            return Err(format_error(
                path,
                format!("unrecognised header line {}: {line:?}", index + 1),
            ));
        };
        header.push((key.trim().to_uppercase(), value.trim().to_string()));
        index += 1;
    }
    let mut sections = Vec::new();
    while index < lines.len() {
        let line = lines[index];
        index += 1;
        if line.is_empty() {
            continue;
        }
        let keyword = line.split_whitespace().next().unwrap_or("").to_uppercase();
        if keyword == "EOF" {
            break;
        }
        if !SECTIONS.contains(&keyword.as_str()) {
            return Err(format_error(
                path,
                format!("unrecognised section at line {index}: {line:?}"),
            ));
        }
        let mut content = Vec::new();
        while index < lines.len() {
            let candidate = lines[index];
            let first = candidate.split_whitespace().next().unwrap_or("");
            if SECTIONS.contains(&first.to_uppercase().as_str()) {
                break;
            }
            if !candidate.is_empty() {
                content.push(candidate.to_string());
            }
            index += 1;
        }
        sections.push((keyword, content));
    }
    Ok((header, sections))
}

fn numbers(rows: &[String], path: &Path, section: &str) -> Result<Vec<f64>, TsplibError> {
    rows.iter()
        .flat_map(|row| row.split_whitespace())
        .map(|token| {
            token
                .parse::<f64>()
                .map_err(|_| format_error(path, format!("{section}: non-numeric value {token:?}")))
        })
        .collect()
}

/// Converts a weight to `i32`, refusing fractions, negatives and overflow.
fn integral(value: f64, path: &Path) -> Result<i32, TsplibError> {
    if value.fract() != 0.0 || value < 0.0 || value > f64::from(i32::MAX) {
        return Err(format_error(
            path,
            format!("edge weight {value} is not a non-negative integer that fits in i32"),
        ));
    }
    Ok(value as i32)
}

/// Expands packed explicit weights into a full symmetric matrix.
fn unpack(values: &[f64], n: usize, layout: &str, path: &Path) -> Result<Vec<i32>, TsplibError> {
    let mut matrix = vec![0i32; n * n];
    // (row, column) positions in file order.
    let positions: Box<dyn Iterator<Item = (usize, usize)>> = match layout {
        "FULL_MATRIX" => Box::new((0..n).flat_map(move |i| (0..n).map(move |j| (i, j)))),
        "UPPER_ROW" => Box::new((0..n).flat_map(move |i| (i + 1..n).map(move |j| (i, j)))),
        "UPPER_DIAG_ROW" => Box::new((0..n).flat_map(move |i| (i..n).map(move |j| (i, j)))),
        "LOWER_DIAG_ROW" => Box::new((0..n).flat_map(move |i| (0..=i).map(move |j| (i, j)))),
        other => {
            return Err(format_error(
                path,
                format!("unsupported EDGE_WEIGHT_FORMAT {other:?}"),
            ));
        }
    };
    let positions: Vec<(usize, usize)> = positions.collect();
    if positions.len() != values.len() {
        return Err(format_error(
            path,
            format!(
                "EDGE_WEIGHT_SECTION has {} values, expected {} for {layout}",
                values.len(),
                positions.len()
            ),
        ));
    }
    let full = layout == "FULL_MATRIX";
    for (&(i, j), &value) in positions.iter().zip(values) {
        let weight = integral(value, path)?;
        matrix[i * n + j] = weight;
        if !full {
            matrix[j * n + i] = weight;
        }
    }
    if full {
        for i in 0..n {
            for j in i + 1..n {
                if matrix[i * n + j] != matrix[j * n + i] {
                    return Err(format_error(
                        path,
                        format!("matrix is asymmetric at ({i}, {j})"),
                    ));
                }
            }
        }
    }
    Ok(matrix)
}

/// Reads a symmetric TSPLIB instance.
///
/// # Errors
///
/// [`TsplibError`] if the file cannot be read, is not a `TSP`, uses an
/// unsupported edge-weight type or layout, or holds non-integral,
/// negative or asymmetric weights.
pub fn read(path: &Path) -> Result<TsplibFile, TsplibError> {
    let text = std::fs::read_to_string(path).map_err(|source| TsplibError::Io {
        path: path.to_path_buf(),
        source,
    })?;
    let (header, sections) = split(&text, path)?;
    let field = |key: &str| {
        header
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value.as_str())
    };
    let section = |key: &str| {
        sections
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, rows)| rows.as_slice())
    };
    let kind = field("TYPE").unwrap_or("");
    if kind.to_uppercase() != "TSP" {
        return Err(format_error(path, format!("TYPE {kind:?} is not TSP")));
    }
    let name = field("NAME")
        .ok_or_else(|| format_error(path, "missing NAME"))?
        .to_string();
    let dimension: usize = field("DIMENSION")
        .ok_or_else(|| format_error(path, "missing DIMENSION"))?
        .parse()
        .map_err(|_| format_error(path, "DIMENSION is not a positive integer"))?;
    if dimension < 8 {
        return Err(format_error(
            path,
            format!("{dimension} cities; at least eight are needed (the double bridge needs room)"),
        ));
    }
    let weight_type = field("EDGE_WEIGHT_TYPE")
        .unwrap_or("EXPLICIT")
        .to_uppercase();
    let weights = match weight_type.as_str() {
        "EXPLICIT" => {
            let layout = field("EDGE_WEIGHT_FORMAT")
                .ok_or_else(|| format_error(path, "EXPLICIT without EDGE_WEIGHT_FORMAT"))?
                .to_uppercase();
            let rows = section("EDGE_WEIGHT_SECTION")
                .ok_or_else(|| format_error(path, "EXPLICIT without EDGE_WEIGHT_SECTION"))?;
            let values = numbers(rows, path, "EDGE_WEIGHT_SECTION")?;
            Weights::Explicit(unpack(&values, dimension, &layout, path)?)
        }
        "EUC_2D" | "ATT" | "GEO" => {
            let rows = section("NODE_COORD_SECTION")
                .ok_or_else(|| format_error(path, "missing NODE_COORD_SECTION"))?;
            let values = numbers(rows, path, "NODE_COORD_SECTION")?;
            if values.len() != 3 * dimension {
                return Err(format_error(
                    path,
                    format!(
                        "NODE_COORD_SECTION has {} values, expected {}",
                        values.len(),
                        3 * dimension
                    ),
                ));
            }
            let kind = match weight_type.as_str() {
                "EUC_2D" => CoordinateKind::Euc2d,
                "ATT" => CoordinateKind::Att,
                _ => CoordinateKind::Geo,
            };
            let points = values
                .as_chunks::<3>()
                .0
                .iter()
                .map(|row| match kind {
                    CoordinateKind::Geo => [
                        super::distance::geo_radians(row[1]),
                        super::distance::geo_radians(row[2]),
                    ],
                    _ => [row[1], row[2]],
                })
                .collect();
            Weights::Coordinates { kind, points }
        }
        other => {
            return Err(format_error(
                path,
                format!("unsupported EDGE_WEIGHT_TYPE {other:?}"),
            ));
        }
    };
    Ok(TsplibFile {
        name,
        dimension,
        weights,
    })
}

/// Reads the first tour of a TSPLIB tour file, as zero-based cities.
///
/// # Errors
///
/// [`TsplibError`] if the file cannot be read, has no tour, or the tour is
/// not a permutation of `0..dimension`.
pub fn read_tour(path: &Path, dimension: usize) -> Result<Vec<u32>, TsplibError> {
    let text = std::fs::read_to_string(path).map_err(|source| TsplibError::Io {
        path: path.to_path_buf(),
        source,
    })?;
    let (_header, sections) = split(&text, path)?;
    let rows = sections
        .iter()
        .find(|(name, _)| name == "TOUR_SECTION")
        .map(|(_, rows)| rows)
        .ok_or_else(|| format_error(path, "no TOUR_SECTION"))?;
    let mut tour = Vec::with_capacity(dimension);
    for token in rows.iter().flat_map(|row| row.split_whitespace()) {
        let value: i64 = token
            .parse()
            .map_err(|_| format_error(path, format!("tour entry {token:?} is not an integer")))?;
        if value == -1 {
            break;
        }
        if value < 1 || value as usize > dimension {
            return Err(format_error(path, format!("city {value} out of range")));
        }
        tour.push((value - 1) as u32);
    }
    let mut seen = vec![false; dimension];
    for &city in &tour {
        if std::mem::replace(&mut seen[city as usize], true) {
            return Err(format_error(path, format!("city {} repeated", city + 1)));
        }
    }
    if tour.len() != dimension {
        return Err(format_error(
            path,
            format!("tour has {} cities, expected {dimension}", tour.len()),
        ));
    }
    Ok(tour)
}
