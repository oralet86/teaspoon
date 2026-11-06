//! TSPLIB distance functions (Reinelt 1995, "TSPLIB 95", section 2).
//!
//! Every function returns an integer distance exactly as the TSPLIB
//! reference code does, so listed optima stay valid.

/// TSPLIB's value of pi for `GEO` distances.  The reference code uses this
/// truncated constant, not the exact value; with the exact value some
/// gr96/gr202/gr431/gr666/ali535 distances change by one (checked
/// 2026-10-31, `ideas/rust-port.md` §4.1).
#[allow(clippy::approx_constant)] // The truncation is the point.
pub const TSPLIB_PI: f64 = 3.141592;

/// Earth radius of the TSPLIB `GEO` definition, in kilometres.
pub const GEO_RADIUS_KM: f64 = 6378.388;

/// The TSPLIB `nint`: round half up.
fn nint(value: f64) -> f64 {
    (value + 0.5).floor()
}

/// `EUC_2D`: Euclidean distance rounded to the nearest integer.
pub fn euc_2d(a: [f64; 2], b: [f64; 2]) -> i64 {
    let dx = a[0] - b[0];
    let dy = a[1] - b[1];
    nint((dx * dx + dy * dy).sqrt()) as i64
}

/// `ATT`: the pseudo-Euclidean distance of att48 and att532.
pub fn att(a: [f64; 2], b: [f64; 2]) -> i64 {
    let dx = a[0] - b[0];
    let dy = a[1] - b[1];
    let raw = ((dx * dx + dy * dy) / 10.0).sqrt();
    let rounded = nint(raw);
    (if rounded < raw {
        rounded + 1.0
    } else {
        rounded
    }) as i64
}

/// Converts a TSPLIB `DDD.MM` coordinate (degrees, minutes) to radians.
pub fn geo_radians(value: f64) -> f64 {
    let degrees = value.trunc();
    let minutes = value - degrees;
    TSPLIB_PI * (degrees + 5.0 * minutes / 3.0) / 180.0
}

/// `GEO`: great-circle distance in whole kilometres between two points
/// given as (latitude, longitude) in radians (see [`geo_radians`]).
pub fn geo(a: [f64; 2], b: [f64; 2]) -> i64 {
    let q1 = (a[1] - b[1]).cos();
    let q2 = (a[0] - b[0]).cos();
    let q3 = (a[0] + b[0]).cos();
    let argument = (0.5 * ((1.0 + q1) * q2 - (1.0 - q1) * q3)).clamp(-1.0, 1.0);
    (GEO_RADIUS_KM * argument.acos() + 1.0).floor() as i64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn euclidean_rounds_half_up() {
        assert_eq!(euc_2d([0.0, 0.0], [3.0, 4.0]), 5);
        assert_eq!(euc_2d([0.0, 0.0], [0.5, 0.0]), 1);
        assert_eq!(euc_2d([0.0, 0.0], [0.49, 0.0]), 0);
    }

    #[test]
    fn att_rounds_up_when_nint_is_below() {
        // sqrt(1000 / 10) = 10 exactly; sqrt(1010 / 10) = 10.05 -> 11.
        assert_eq!(att([0.0, 0.0], [30.0, 10.0]), 10);
        assert_eq!(att([0.0, 0.0], [31.0, 7.0]), 11);
    }

    #[test]
    fn geo_of_a_point_to_itself_is_one() {
        let p = [geo_radians(14.55), geo_radians(-23.31)];
        assert_eq!(geo(p, p), 1);
    }
}
