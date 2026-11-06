//! Tours (`ideas/rust-port.md` §5).
//!
//! A [`Tour`] is a closed tour with an incrementally kept length and an
//! undo log, over one of two [`Layout`]s: [`array::Array`] (`order`/`pos`
//! arrays) and [`blocks::Blocks`] (segments of about √n cities with
//! reversal bits, O(√n) per reversal).
//!
//! **Logical orientation.**  Both layouts keep a global reversal bit, so
//! reversing the path `a .. b` always means the same thing: the cities
//! outside the path keep their direction, whichever side the layout
//! physically rewrites.  `next`/`prev` are therefore a function of the
//! sequence of operations alone, identical in both layouts, and undoing a
//! change restores the exact logical tour.  Operators see only the logical
//! interface (`next`, `prev`, `steps`, `walk`, `between`), so they behave
//! identically on both layouts.

pub mod array;
pub mod blocks;

use crate::City;
use crate::instance::storage::Dist;

/// The physical representation of a tour.  Every method is logical: it
/// describes the cyclic sequence and its orientation, never the storage.
pub trait Layout: Clone + Send + Sync {
    /// Name recorded in provenance.
    const NAME: &'static str;

    /// A layout visiting `order` forward.
    fn from_order(order: &[City]) -> Self;

    /// Number of cities.
    fn n(&self) -> usize;

    /// The city after `c`.
    fn next(&self, c: City) -> City;

    /// The city before `c`.
    fn prev(&self, c: City) -> City;

    /// Forward steps from `a` to `c` (0 when equal).
    fn steps(&self, a: City, c: City) -> usize;

    /// The city `k` steps forward from `c` (`k < n`).
    fn walk(&self, c: City, k: usize) -> City;

    /// Reverses the forward path `a .. b` (inclusive); the rest keeps its
    /// direction.  The path must not be the whole tour.  Returns the
    /// number of storage positions rewritten.
    fn reverse(&mut self, a: City, b: City) -> u64;

    /// Exchanges the places of cities `a` and `b`.  Returns positions
    /// rewritten.
    fn swap(&mut self, a: City, b: City) -> u64;

    /// Checks internal consistency.
    ///
    /// # Errors
    ///
    /// Describing the first inconsistency.
    fn check_consistent(&self) -> Result<(), String>;
}

/// One logged change, undone in reverse order.
#[derive(Debug, Clone)]
enum Change {
    /// The path `a .. b` was reversed; undone by reversing `b .. a`.
    Reverse(City, City),
    /// Two cities exchanged places; undone by exchanging them again.
    Swap(City, City),
    /// The whole order was replaced; holds the previous order from city 0.
    Replace(Vec<City>),
}

/// One recorded change of a [`Replay`].
#[derive(Debug, Clone)]
enum Step {
    Reverse(City, City),
    Swap(City, City),
    /// A whole-order replacement: the order before (for the undo log) and
    /// after.
    Replace {
        previous: Vec<City>,
        next: Vec<City>,
    },
}

/// The changes one application made, recorded by [`Tour::replay_since`]
/// to be applied again by [`Tour::replay`] (restore-time measurement).
#[derive(Debug, Clone)]
pub struct Replay {
    steps: Vec<Step>,
    delta: i64,
}

/// A point in the undo log to return to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Mark {
    log: usize,
    length: i64,
}

/// A closed tour: a layout, its length, and an undo log of changes.
#[derive(Debug, Clone)]
pub struct Tour<L: Layout> {
    layout: L,
    length: i64,
    log: Vec<Change>,
}

impl<L: Layout> Tour<L> {
    /// A tour visiting `order`, with its length computed from `dist`.
    ///
    /// # Panics
    ///
    /// If `order` is not a permutation of `0..n` (callers validate input
    /// orders first, see [`validate_order`]).
    pub fn new(order: &[City], dist: &impl Dist) -> Self {
        validate_order(order, dist.n()).expect("tour order is a permutation");
        Self {
            layout: L::from_order(order),
            length: crate::instance::tour_length(dist, order),
            log: Vec::new(),
        }
    }

    /// Number of cities.
    #[inline(always)]
    pub fn n(&self) -> usize {
        self.layout.n()
    }

    /// The current length.
    #[inline(always)]
    pub fn length(&self) -> i64 {
        self.length
    }

    /// Adds `delta` to the tracked length (operators keep it current).
    #[inline(always)]
    pub fn add_length(&mut self, delta: i64) {
        self.length += delta;
    }

    /// The city after `c`.
    #[inline(always)]
    pub fn next(&self, c: City) -> City {
        self.layout.next(c)
    }

    /// The city before `c`.
    #[inline(always)]
    pub fn prev(&self, c: City) -> City {
        self.layout.prev(c)
    }

    /// The neighbour of `c`: the next city if `forward`, else the previous.
    #[inline(always)]
    pub fn succ_in(&self, c: City, forward: bool) -> City {
        if forward { self.next(c) } else { self.prev(c) }
    }

    /// Forward steps from `a` to `c`.
    #[inline(always)]
    pub fn steps(&self, a: City, c: City) -> usize {
        self.layout.steps(a, c)
    }

    /// The city `k` steps forward from `c`, for any `k` (taken mod n).
    #[inline(always)]
    pub fn walk(&self, c: City, k: usize) -> City {
        self.layout.walk(c, k % self.n())
    }

    /// Whether `b` lies on the forward path from `a` to `c` (inclusive).
    #[inline(always)]
    pub fn between(&self, a: City, b: City, c: City) -> bool {
        self.steps(a, b) <= self.steps(a, c)
    }

    /// Reverses the forward path `a .. b` and logs it.  Returns positions
    /// rewritten.
    #[inline]
    pub fn reverse(&mut self, a: City, b: City) -> u64 {
        if a == b {
            return 0;
        }
        self.log.push(Change::Reverse(a, b));
        self.layout.reverse(a, b)
    }

    /// Exchanges the places of `a` and `b` and logs it.
    #[inline]
    pub fn swap(&mut self, a: City, b: City) -> u64 {
        self.log.push(Change::Swap(a, b));
        self.layout.swap(a, b)
    }

    /// Replaces the whole order (visited forward from `order[0]`) and logs
    /// the previous one.  Returns positions rewritten (n).
    pub fn replace(&mut self, order: &[City]) -> u64 {
        debug_assert!(validate_order(order, self.n()).is_ok());
        self.log.push(Change::Replace(self.order()));
        self.layout = L::from_order(order);
        self.n() as u64
    }

    /// The current point of the undo log.
    #[inline]
    pub fn mark(&self) -> Mark {
        Mark {
            log: self.log.len(),
            length: self.length,
        }
    }

    /// Undoes every change after `mark` and restores its length: the
    /// logical tour is then exactly what it was at `mark`.  Returns the
    /// storage positions rewritten.
    pub fn undo_to(&mut self, mark: Mark) -> u64 {
        let mut moved = 0;
        while self.log.len() > mark.log {
            moved += match self.log.pop().expect("log is longer than the mark") {
                Change::Reverse(a, b) => self.layout.reverse(b, a),
                Change::Swap(a, b) => self.layout.swap(a, b),
                Change::Replace(order) => {
                    self.layout = L::from_order(&order);
                    self.n() as u64
                }
            };
        }
        self.length = mark.length;
        moved
    }

    /// The changes made since `mark`, as a [`Replay`] that
    /// [`Tour::replay`] applies again to the tour as it was at `mark`.
    ///
    /// This exists to *measure* the cost of a restore: replaying an
    /// application's changes costs what undoing them costs (a reversal
    /// replayed rewrites the same positions as its undo; a whole-order
    /// replacement replayed is one rebuild, as its undo is, plus a copy of
    /// the order for the undo log), so a timed batch of replays each
    /// followed by [`Tour::undo_to`] is about two restores per application,
    /// without the operator's search.  Untimed: it walks a copy of the
    /// tour back to `mark`.
    pub fn replay_since(&self, mark: Mark) -> Replay {
        let mut copy = self.clone();
        let mut steps = Vec::with_capacity(self.log.len() - mark.log);
        while copy.log.len() > mark.log {
            let step = match copy.log.last().expect("log is longer than the mark") {
                Change::Reverse(a, b) => Step::Reverse(*a, *b),
                Change::Swap(a, b) => Step::Swap(*a, *b),
                Change::Replace(previous) => Step::Replace {
                    previous: previous.clone(),
                    next: copy.order(),
                },
            };
            steps.push(step);
            let index = copy.log.len() - 1;
            copy.undo_to(Mark {
                log: index,
                length: copy.length,
            });
        }
        steps.reverse();
        Replay {
            steps,
            delta: self.length - mark.length,
        }
    }

    /// Applies a [`Replay`] taken from this tour at the same state, logging
    /// every change so that [`Tour::undo_to`] restores the state.
    pub fn replay(&mut self, replay: &Replay) {
        for step in &replay.steps {
            match step {
                Step::Reverse(a, b) => {
                    self.log.push(Change::Reverse(*a, *b));
                    self.layout.reverse(*a, *b);
                }
                Step::Swap(a, b) => {
                    self.log.push(Change::Swap(*a, *b));
                    self.layout.swap(*a, *b);
                }
                Step::Replace { previous, next } => {
                    self.log.push(Change::Replace(previous.clone()));
                    self.layout = L::from_order(next);
                }
            }
        }
        self.length += replay.delta;
    }

    /// Forgets the undo log (changes become permanent).
    #[inline]
    pub fn clear_log(&mut self) {
        self.log.clear();
    }

    /// Endpoints of the reversals logged after `mark` (the cities whose
    /// tour neighbours those changes touched, with their current
    /// neighbours), for work queues.
    pub fn touched_since(&self, mark: Mark, out: &mut Vec<City>) {
        for change in &self.log[mark.log..] {
            match *change {
                Change::Reverse(a, b) | Change::Swap(a, b) => {
                    for city in [a, b] {
                        out.extend([city, self.next(city), self.prev(city)]);
                    }
                }
                Change::Replace(_) => {}
            }
        }
    }

    /// Rebuilds the storage from the logical tour, leaving the tour, its
    /// length and its undo log as they are.  Undo restores a tour
    /// logically, but the two-level list's blocks can end up split and
    /// oriented differently, so the same application can then move a
    /// different number of positions; timing code calls this before each
    /// timed sample so every sample starts from the same storage.
    pub fn normalize(&mut self) {
        let order = self.order();
        self.layout = L::from_order(&order);
    }

    /// The visiting order forward from city 0.
    pub fn order(&self) -> Vec<City> {
        let mut order = Vec::with_capacity(self.n());
        let mut city = 0;
        for _ in 0..self.n() {
            order.push(city);
            city = self.next(city);
        }
        order
    }

    /// A 64-bit hash of the cycle, independent of rotation and orientation:
    /// read from city 0 towards its smaller neighbour.
    pub fn hash(&self) -> u64 {
        let forward = self.next(0) < self.prev(0);
        let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
        let mut city: City = 0;
        for _ in 0..self.n() {
            for byte in city.to_le_bytes() {
                hash ^= u64::from(byte);
                hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
            }
            city = self.succ_in(city, forward);
        }
        hash
    }

    /// Checks the layout, the cycle and the tracked length.
    ///
    /// # Errors
    ///
    /// Describing the first inconsistency.
    pub fn check(&self, dist: &impl Dist) -> Result<(), String> {
        self.layout.check_consistent()?;
        let order = self.order();
        validate_order(&order, self.n())?;
        for &city in &order {
            if self.prev(self.next(city)) != city {
                return Err(format!("prev(next({city})) != {city}"));
            }
        }
        let recomputed = crate::instance::tour_length(dist, &order);
        if recomputed != self.length {
            return Err(format!("tracked length {} != {recomputed}", self.length));
        }
        Ok(())
    }

    /// Whether two tours are the same logical tour (same `next` everywhere).
    pub fn same_as(&self, other: &Self) -> bool {
        self.n() == other.n() && (0..self.n() as City).all(|c| self.next(c) == other.next(c))
    }
}

/// Checks that `order` is a permutation of `0..n`.
///
/// # Errors
///
/// Naming the problem.
pub fn validate_order(order: &[City], n: usize) -> Result<(), String> {
    if order.len() != n {
        return Err(format!("order has {} cities, expected {n}", order.len()));
    }
    let mut seen = vec![false; n];
    for &city in order {
        let slot = seen
            .get_mut(city as usize)
            .ok_or_else(|| format!("city {city} out of range"))?;
        if std::mem::replace(slot, true) {
            return Err(format!("city {city} repeated"));
        }
    }
    Ok(())
}
