//! The two-level layout: segments ("blocks") of about √n cities, each with
//! a reversal bit, in a cyclic order of blocks (Fredman et al. 1995; the
//! segment list of LKH's two-level doubly-linked list).
//!
//! A reversal splits at most two blocks so the path starts and ends on
//! block boundaries, then reverses the order of the blocks it covers and
//! toggles their bits: O(√n) work.  Splits add blocks; when there are too
//! many, the layout is rebuilt into even blocks (O(n), amortised O(√n)).
//! As in [`super::array::Array`], the shorter of the path and its
//! complement is rewritten, with a global reversal bit keeping the logical
//! result the same.
//!
//! "Storage order" below is the order of blocks in `order`, each read
//! forward, or backward when its bit is set.  Blocks are kept in a vector
//! with ranks and offsets rather than LKH's linked list of parents; the
//! cost per reversal is the same O(√n) (`rust/DEVIATIONS.md`).

use super::Layout;
use crate::City;

/// Cities in blocks of about √n with reversal bits.
#[derive(Debug, Clone)]
pub struct Blocks {
    n: usize,
    /// Block size of a fresh layout.
    target: usize,
    /// Block count above which the layout is rebuilt.
    limit: usize,
    /// Stored cities of every block.
    cities: Vec<Vec<City>>,
    /// Reversal bit of every block.
    flipped: Vec<bool>,
    /// Index of every block in `order`.
    rank: Vec<u32>,
    /// Storage position of every block's first city.
    offset: Vec<u32>,
    /// Block ids in storage order.
    order: Vec<u32>,
    /// Block of every city.
    block_of: Vec<u32>,
    /// Index of every city in its block's stored cities.
    index: Vec<u32>,
    /// Global reversal bit: the logical direction is storage-backward.
    reversed: bool,
}

impl Blocks {
    /// A layout of the storage sequence `sequence` with global bit
    /// `reversed`, in fresh even blocks.
    fn from_storage(sequence: &[City], reversed: bool) -> Self {
        let n = sequence.len();
        let target = (n as f64).sqrt().ceil().max(1.0) as usize;
        let cities: Vec<Vec<City>> = sequence.chunks(target).map(<[City]>::to_vec).collect();
        let count = cities.len();
        let mut layout = Self {
            n,
            target,
            limit: 2 * count + 4,
            cities,
            flipped: vec![false; count],
            rank: vec![0; count],
            offset: vec![0; count],
            order: (0..count as u32).collect(),
            block_of: vec![0; n],
            index: vec![0; n],
            reversed,
        };
        for block in 0..count {
            layout.reindex(block);
        }
        layout.renumber();
        layout
    }

    /// Updates `block_of` and `index` for every city of `block`.
    fn reindex(&mut self, block: usize) {
        for (index, &city) in self.cities[block].iter().enumerate() {
            self.block_of[city as usize] = block as u32;
            self.index[city as usize] = index as u32;
        }
    }

    /// Recomputes ranks and offsets from `order`.
    fn renumber(&mut self) {
        let mut offset = 0u32;
        for (rank, &block) in self.order.iter().enumerate() {
            self.rank[block as usize] = rank as u32;
            self.offset[block as usize] = offset;
            offset += self.cities[block as usize].len() as u32;
        }
    }

    /// Index of `c` within its block, in storage direction.
    #[inline(always)]
    fn inner(&self, c: City) -> usize {
        let block = self.block_of[c as usize] as usize;
        let index = self.index[c as usize] as usize;
        if self.flipped[block] {
            self.cities[block].len() - 1 - index
        } else {
            index
        }
    }

    /// Storage position of `c`.
    #[inline(always)]
    fn storage(&self, c: City) -> usize {
        self.offset[self.block_of[c as usize] as usize] as usize + self.inner(c)
    }

    /// Logical position of `c`.
    #[inline(always)]
    fn logical(&self, c: City) -> usize {
        let position = self.storage(c);
        if self.reversed {
            self.n - 1 - position
        } else {
            position
        }
    }

    /// The city at storage position `position`.
    fn at(&self, position: usize) -> City {
        let rank = self
            .order
            .partition_point(|&block| self.offset[block as usize] as usize <= position)
            - 1;
        let block = self.order[rank] as usize;
        let inner = position - self.offset[block] as usize;
        let stored = if self.flipped[block] {
            self.cities[block].len() - 1 - inner
        } else {
            inner
        };
        self.cities[block][stored]
    }

    /// First city of `block` in storage direction.
    #[inline(always)]
    fn first_of(&self, block: usize) -> City {
        let cities = &self.cities[block];
        if self.flipped[block] {
            cities[cities.len() - 1]
        } else {
            cities[0]
        }
    }

    /// Last city of `block` in storage direction.
    #[inline(always)]
    fn last_of(&self, block: usize) -> City {
        let cities = &self.cities[block];
        if self.flipped[block] {
            cities[0]
        } else {
            cities[cities.len() - 1]
        }
    }

    /// The block after `block` in storage order.
    #[inline(always)]
    fn block_after(&self, block: usize) -> usize {
        let rank = self.rank[block] as usize + 1;
        self.order[if rank == self.order.len() { 0 } else { rank }] as usize
    }

    /// The block before `block` in storage order.
    #[inline(always)]
    fn block_before(&self, block: usize) -> usize {
        let rank = self.rank[block] as usize;
        self.order[if rank == 0 {
            self.order.len() - 1
        } else {
            rank - 1
        }] as usize
    }

    /// The city after `c` in storage order.
    #[inline(always)]
    fn storage_next(&self, c: City) -> City {
        let block = self.block_of[c as usize] as usize;
        let index = self.index[c as usize] as usize;
        let cities = &self.cities[block];
        if self.flipped[block] {
            if index > 0 {
                return cities[index - 1];
            }
        } else if index + 1 < cities.len() {
            return cities[index + 1];
        }
        self.first_of(self.block_after(block))
    }

    /// The city before `c` in storage order.
    #[inline(always)]
    fn storage_prev(&self, c: City) -> City {
        let block = self.block_of[c as usize] as usize;
        let index = self.index[c as usize] as usize;
        let cities = &self.cities[block];
        if self.flipped[block] {
            if index + 1 < cities.len() {
                return cities[index + 1];
            }
        } else if index > 0 {
            return cities[index - 1];
        }
        self.last_of(self.block_before(block))
    }

    /// Splits the block of `c` so that `c` is the first city of a block (in
    /// storage direction).  Returns cities re-indexed.
    fn split_before(&mut self, c: City) -> u64 {
        let block = self.block_of[c as usize] as usize;
        if self.first_of(block) == c {
            return 0;
        }
        let inner = self.inner(c);
        let length = self.cities[block].len();
        let new_block = self.cities.len();
        // The storage part from `c` on moves to a new block after `block`.
        let (kept, moved) = if self.flipped[block] {
            let tail = self.cities[block].split_off(length - inner);
            let head = std::mem::replace(&mut self.cities[block], tail);
            (self.cities[block].len(), head)
        } else {
            let tail = self.cities[block].split_off(inner);
            (inner, tail)
        };
        let moved_count = moved.len();
        self.cities.push(moved);
        self.flipped.push(self.flipped[block]);
        self.rank.push(0);
        self.offset.push(0);
        self.reindex(block);
        self.reindex(new_block);
        self.order
            .insert(self.rank[block] as usize + 1, new_block as u32);
        self.renumber();
        (kept + moved_count) as u64
    }

    /// Reverses the storage path `first .. last` (at most half the tour).
    fn reverse_storage(&mut self, first: City, last: City) -> u64 {
        let mut moved = self.split_before(first);
        let after_last = self.storage_next(last);
        moved += self.split_before(after_last);
        let count = self.order.len();
        let start = self.rank[self.block_of[first as usize] as usize] as usize;
        let end = self.rank[self.block_of[last as usize] as usize] as usize;
        let blocks = (end + count - start) % count + 1;
        for k in 0..blocks / 2 {
            self.order
                .swap((start + k) % count, (end + count - k) % count);
        }
        for k in 0..blocks {
            let block = self.order[(start + k) % count] as usize;
            self.flipped[block] = !self.flipped[block];
        }
        self.renumber();
        moved += blocks as u64;
        if self.order.len() > self.limit {
            let sequence = self.storage_sequence();
            *self = Self::from_storage(&sequence, self.reversed);
            moved += self.n as u64;
        }
        moved
    }

    /// Every city in storage order, from the first block.
    fn storage_sequence(&self) -> Vec<City> {
        let mut sequence = Vec::with_capacity(self.n);
        for &block in &self.order {
            let cities = &self.cities[block as usize];
            if self.flipped[block as usize] {
                sequence.extend(cities.iter().rev());
            } else {
                sequence.extend(cities);
            }
        }
        sequence
    }
}

impl Layout for Blocks {
    const NAME: &'static str = "two_level";

    fn from_order(order: &[City]) -> Self {
        Self::from_storage(order, false)
    }

    #[inline(always)]
    fn n(&self) -> usize {
        self.n
    }

    #[inline(always)]
    fn next(&self, c: City) -> City {
        if self.reversed {
            self.storage_prev(c)
        } else {
            self.storage_next(c)
        }
    }

    #[inline(always)]
    fn prev(&self, c: City) -> City {
        if self.reversed {
            self.storage_next(c)
        } else {
            self.storage_prev(c)
        }
    }

    #[inline(always)]
    fn steps(&self, a: City, c: City) -> usize {
        (self.logical(c) + self.n - self.logical(a)) % self.n
    }

    fn walk(&self, c: City, k: usize) -> City {
        let logical = (self.logical(c) + k) % self.n;
        self.at(if self.reversed {
            self.n - 1 - logical
        } else {
            logical
        })
    }

    fn reverse(&mut self, a: City, b: City) -> u64 {
        let (first, last) = if self.reversed { (b, a) } else { (a, b) };
        let n = self.n;
        let span = (self.storage(last) + n - self.storage(first)) % n + 1;
        assert!(span < n, "cannot reverse the whole tour");
        if 2 * span <= n {
            self.reverse_storage(first, last)
        } else {
            let complement_first = self.storage_next(last);
            let complement_last = self.storage_prev(first);
            let moved = self.reverse_storage(complement_first, complement_last);
            self.reversed = !self.reversed;
            moved
        }
    }

    #[inline]
    fn swap(&mut self, a: City, b: City) -> u64 {
        let (block_a, index_a) = (self.block_of[a as usize], self.index[a as usize]);
        let (block_b, index_b) = (self.block_of[b as usize], self.index[b as usize]);
        self.cities[block_a as usize][index_a as usize] = b;
        self.cities[block_b as usize][index_b as usize] = a;
        self.block_of[a as usize] = block_b;
        self.index[a as usize] = index_b;
        self.block_of[b as usize] = block_a;
        self.index[b as usize] = index_a;
        2
    }

    fn check_consistent(&self) -> Result<(), String> {
        let mut offset = 0u32;
        for (rank, &block) in self.order.iter().enumerate() {
            let block = block as usize;
            if self.cities[block].is_empty() {
                return Err(format!("block {block} is empty"));
            }
            if self.rank[block] as usize != rank || self.offset[block] != offset {
                return Err(format!("block {block}: stale rank or offset"));
            }
            for (index, &city) in self.cities[block].iter().enumerate() {
                if self.block_of[city as usize] as usize != block
                    || self.index[city as usize] as usize != index
                {
                    return Err(format!("city {city}: stale block or index"));
                }
            }
            offset += self.cities[block].len() as u32;
        }
        if offset as usize != self.n {
            return Err(format!("blocks hold {offset} cities, expected {}", self.n));
        }
        if self.target == 0 {
            return Err("block size zero".to_string());
        }
        Ok(())
    }
}
