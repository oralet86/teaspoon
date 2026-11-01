//! The array layout: `order` and `pos` arrays and a global reversal bit.
//!
//! A reversal rewrites the shorter of the path and its complement (as
//! `aos/tour.py`); rewriting the complement also toggles the reversal
//! bit, so the logical result is the same either way.

use super::Layout;
use crate::City;

/// `order[k]` is the city at storage position `k`, `pos[c]` the position of
/// city `c`; read backwards when `reversed`.
#[derive(Debug, Clone)]
pub struct Array {
    order: Vec<City>,
    pos: Vec<u32>,
    reversed: bool,
}

impl Array {
    /// Logical position of `c` (0-based along the forward direction).
    #[inline(always)]
    fn logical(&self, c: City) -> usize {
        let position = self.pos[c as usize] as usize;
        if self.reversed {
            self.order.len() - 1 - position
        } else {
            position
        }
    }

    /// Reverses storage positions `start ..= end` (cyclic, `span` long).
    fn reverse_storage(&mut self, mut start: usize, mut end: usize, span: usize) -> u64 {
        let n = self.order.len();
        for _ in 0..span / 2 {
            let first = self.order[start];
            let second = self.order[end];
            self.order[start] = second;
            self.pos[second as usize] = start as u32;
            self.order[end] = first;
            self.pos[first as usize] = end as u32;
            start = if start + 1 < n { start + 1 } else { 0 };
            end = if end > 0 { end - 1 } else { n - 1 };
        }
        (span - span % 2) as u64
    }
}

impl Layout for Array {
    const NAME: &'static str = "array";

    fn from_order(order: &[City]) -> Self {
        let mut pos = vec![0u32; order.len()];
        for (index, &city) in order.iter().enumerate() {
            pos[city as usize] = index as u32;
        }
        Self {
            order: order.to_vec(),
            pos,
            reversed: false,
        }
    }

    #[inline(always)]
    fn n(&self) -> usize {
        self.order.len()
    }

    #[inline(always)]
    fn next(&self, c: City) -> City {
        let n = self.order.len();
        let position = self.pos[c as usize] as usize;
        if self.reversed {
            self.order[if position == 0 { n - 1 } else { position - 1 }]
        } else {
            self.order[if position + 1 == n { 0 } else { position + 1 }]
        }
    }

    #[inline(always)]
    fn prev(&self, c: City) -> City {
        let n = self.order.len();
        let position = self.pos[c as usize] as usize;
        if self.reversed {
            self.order[if position + 1 == n { 0 } else { position + 1 }]
        } else {
            self.order[if position == 0 { n - 1 } else { position - 1 }]
        }
    }

    #[inline(always)]
    fn steps(&self, a: City, c: City) -> usize {
        let n = self.order.len();
        (self.logical(c) + n - self.logical(a)) % n
    }

    #[inline(always)]
    fn walk(&self, c: City, k: usize) -> City {
        let n = self.order.len();
        let logical = (self.logical(c) + k) % n;
        self.order[if self.reversed {
            n - 1 - logical
        } else {
            logical
        }]
    }

    fn reverse(&mut self, a: City, b: City) -> u64 {
        let n = self.order.len();
        // Storage-forward endpoints of the logical path a .. b.
        let (first, last) = if self.reversed { (b, a) } else { (a, b) };
        let start = self.pos[first as usize] as usize;
        let end = self.pos[last as usize] as usize;
        let span = (end + n - start) % n + 1;
        assert!(span < n, "cannot reverse the whole tour");
        if 2 * span <= n {
            self.reverse_storage(start, end, span)
        } else {
            let moved = self.reverse_storage((end + 1) % n, (start + n - 1) % n, n - span);
            self.reversed = !self.reversed;
            moved
        }
    }

    #[inline]
    fn swap(&mut self, a: City, b: City) -> u64 {
        let position_a = self.pos[a as usize];
        let position_b = self.pos[b as usize];
        self.order[position_a as usize] = b;
        self.order[position_b as usize] = a;
        self.pos[a as usize] = position_b;
        self.pos[b as usize] = position_a;
        2
    }

    fn check_consistent(&self) -> Result<(), String> {
        if self.pos.len() != self.order.len() {
            return Err("order and pos differ in length".to_string());
        }
        for (index, &city) in self.order.iter().enumerate() {
            if self.pos.get(city as usize).copied() != Some(index as u32) {
                return Err(format!("pos[{city}] != {index}"));
            }
        }
        Ok(())
    }
}
