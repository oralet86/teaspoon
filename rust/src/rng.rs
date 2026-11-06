//! Seeded random streams (`ideas/rust-port.md` §7.5).
//!
//! Every application gets its own stream, derived from (run seed, state
//! label, operator, anchor, repeat), so it can be replayed exactly in any
//! order and on any thread.  The generator is xoshiro256++; its 256-bit
//! seed is four SplitMix64 outputs of a chain that mixes in each key.
//! Labels enter through the first 8 bytes of their SHA-256.

use rand_core::{Rng, SeedableRng};
use rand_xoshiro::Xoshiro256PlusPlus;
use sha2::{Digest, Sha256};

/// Name of the generator and seeding scheme, recorded in provenance.
pub const SCHEME: &str = "xoshiro256++ seeded by SplitMix64 over (seed, sha256_64(label), keys...)";

/// One SplitMix64 step (Steele, Lea and Flood 2014).
fn splitmix64(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

/// The first 8 bytes of a label's SHA-256, little-endian.
pub fn label_hash(label: &str) -> u64 {
    let digest = Sha256::digest(label.as_bytes());
    u64::from_le_bytes(
        digest[..8]
            .try_into()
            .expect("a SHA-256 digest has 32 bytes"),
    )
}

/// A seeded random stream with the draws the operators need.
#[derive(Debug, Clone)]
pub struct Stream(Xoshiro256PlusPlus);

impl Stream {
    /// The stream for `seed`, `label` and further integer keys (for example
    /// operator, anchor and repeat).
    pub fn new(seed: u64, label: &str, keys: &[u64]) -> Self {
        let mut state = seed;
        splitmix64(&mut state);
        state ^= label_hash(label);
        for &key in keys {
            splitmix64(&mut state);
            state ^= key;
        }
        let mut bytes = [0u8; 32];
        for chunk in bytes.as_chunks_mut::<8>().0 {
            chunk.copy_from_slice(&splitmix64(&mut state).to_le_bytes());
        }
        Self(Xoshiro256PlusPlus::from_seed(bytes))
    }

    /// The next 64 random bits.
    #[inline]
    pub fn next_u64(&mut self) -> u64 {
        self.0.next_u64()
    }

    /// A uniform integer in `0..bound`, without modulo bias (Lemire 2019).
    ///
    /// # Panics
    ///
    /// If `bound` is zero.
    #[inline]
    pub fn below(&mut self, bound: u64) -> u64 {
        assert!(bound > 0, "below(0) has no values");
        let mut product = u128::from(self.next_u64()) * u128::from(bound);
        let mut low = product as u64;
        if low < bound {
            let threshold = bound.wrapping_neg() % bound;
            while low < threshold {
                product = u128::from(self.next_u64()) * u128::from(bound);
                low = product as u64;
            }
        }
        (product >> 64) as u64
    }

    /// A uniform index in `0..bound`.
    #[inline]
    pub fn index(&mut self, bound: usize) -> usize {
        self.below(bound as u64) as usize
    }

    /// A uniform float in `[0, 1)` with 53 random bits.
    #[inline]
    pub fn unit(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 * (1.0 / (1u64 << 53) as f64)
    }

    /// One draw from a `rand` distribution, with this stream's generator.
    pub fn sample_from<T>(&mut self, distribution: &impl rand::distr::Distribution<T>) -> T {
        distribution.sample(&mut self.0)
    }

    /// Shuffles `items` in place (Fisher-Yates).
    pub fn shuffle<T>(&mut self, items: &mut [T]) {
        for last in (1..items.len()).rev() {
            let other = self.index(last + 1);
            items.swap(last, other);
        }
    }

    /// `count` distinct values from `0..population`, in draw order.
    ///
    /// # Panics
    ///
    /// If `count > population`.
    pub fn sample(&mut self, population: usize, count: usize) -> Vec<usize> {
        assert!(count <= population, "cannot draw {count} of {population}");
        let mut chosen: Vec<usize> = Vec::with_capacity(count);
        while chosen.len() < count {
            let value = self.index(population);
            if !chosen.contains(&value) {
                chosen.push(value);
            }
        }
        chosen
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn streams_depend_on_every_key() {
        let base = Stream::new(0, "kroA100:s0:opt2", &[3, 7, 0]).next_u64();
        assert_eq!(
            base,
            Stream::new(0, "kroA100:s0:opt2", &[3, 7, 0]).next_u64()
        );
        assert_ne!(
            base,
            Stream::new(1, "kroA100:s0:opt2", &[3, 7, 0]).next_u64()
        );
        assert_ne!(
            base,
            Stream::new(0, "kroA100:s0:opt3", &[3, 7, 0]).next_u64()
        );
        assert_ne!(
            base,
            Stream::new(0, "kroA100:s0:opt2", &[3, 7, 1]).next_u64()
        );
        assert_ne!(
            base,
            Stream::new(0, "kroA100:s0:opt2", &[7, 3, 0]).next_u64()
        );
    }

    #[test]
    fn below_stays_in_range_and_covers_it() {
        let mut stream = Stream::new(5, "test", &[]);
        let mut seen = [false; 7];
        for _ in 0..1000 {
            seen[stream.index(7)] = true;
        }
        assert!(seen.iter().all(|&value| value));
    }

    #[test]
    fn sample_is_distinct() {
        let mut stream = Stream::new(1, "sample", &[]);
        let mut values = stream.sample(10, 10);
        values.sort_unstable();
        assert_eq!(values, (0..10).collect::<Vec<_>>());
    }
}
