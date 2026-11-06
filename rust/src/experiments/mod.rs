//! Experiments, one module per subcommand (`ideas/rust-port.md` §8), and
//! the summary statistics they share.

pub mod common;
pub mod compare;
pub mod e0;
pub mod marginal;
pub mod population;
pub mod rollouts;
pub mod states;

/// Median of `values` (mean of the middle two for an even count), from
/// `statrs`.
///
/// # Panics
///
/// If `values` is empty.
pub fn median(values: &[f64]) -> f64 {
    use statrs::statistics::{Data, Median};
    assert!(!values.is_empty(), "median of no values");
    Data::new(values.to_vec()).median()
}

/// The `q` quantile of `values`, from `statrs`: Hyndman and Fan's
/// definition 8 (approximately median-unbiased; R's `type = 8`).  Until
/// 2026-11-02 the port used definition 7 (NumPy's default, linear between
/// order statistics), so quantiles of few values differ slightly from the
/// Python reference's.
///
/// # Panics
///
/// If `values` is empty or `q` is outside `[0, 1]`.
pub fn quantile(values: &[f64], q: f64) -> f64 {
    use statrs::statistics::{Data, OrderStatistics};
    assert!(!values.is_empty(), "quantile of no values");
    assert!((0.0..=1.0).contains(&q), "quantile {q} outside [0, 1]");
    Data::new(values.to_vec()).quantile(q)
}

/// Arithmetic mean, from `statrs`.
///
/// # Panics
///
/// If `values` is empty.
pub fn mean(values: &[f64]) -> f64 {
    use statrs::statistics::Statistics;
    assert!(!values.is_empty(), "mean of no values");
    values.mean()
}

/// Sample standard deviation (n − 1 denominator), from `statrs`.
///
/// # Panics
///
/// If `values` has fewer than two values.
pub fn standard_deviation(values: &[f64]) -> f64 {
    use statrs::statistics::Statistics;
    assert!(
        values.len() >= 2,
        "standard deviation of {} values",
        values.len()
    );
    values.std_dev()
}

/// Coefficient of variation: sample standard deviation over the mean
/// (zero for fewer than two values).
pub fn coefficient_of_variation(values: &[f64]) -> f64 {
    if values.len() < 2 {
        return 0.0;
    }
    standard_deviation(values) / mean(values)
}

/// The two-sided 95% confidence interval of a coefficient of variation
/// estimated from `count` values, treating the mean as known and the
/// values as normal: the chi-square interval of the standard deviation,
/// `cv · sqrt((count − 1) / χ²)` with the chi-square quantiles of `statrs`.
/// `None` for fewer than two values.
pub fn cv_interval(cv: f64, count: usize) -> Option<(f64, f64)> {
    if count < 2 {
        return None;
    }
    use statrs::distribution::{ChiSquared, ContinuousCDF};
    let df = (count - 1) as f64;
    let chi_square = ChiSquared::new(df).expect("positive degrees of freedom");
    Some((
        cv * (df / chi_square.inverse_cdf(0.975)).sqrt(),
        cv * (df / chi_square.inverse_cdf(0.025)).sqrt(),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quantiles_interpolate() {
        let values = [4.0, 1.0, 3.0, 2.0];
        assert_eq!(median(&values), 2.5);
        assert_eq!(quantile(&values, 0.0), 1.0);
        assert_eq!(quantile(&values, 1.0), 4.0);
        assert!((coefficient_of_variation(&[1.0, 1.0, 1.0])).abs() < 1e-12);
    }
}
