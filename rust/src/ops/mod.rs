//! The operators O1-O21 (`ideas/rust-port.md` §6), ported from
//! `aos/operators.py` with the move definitions and acceptance rules kept.
//!
//! Every application takes the tour, the instance ([`Problem`]), its random
//! stream, the work counters and an optional anchor, and returns the length
//! change (never positive) and the anchor used.  Moves apply only an
//! improving move; trials keep their result only if the tour got shorter.
//! A non-improving application leaves the logical tour exactly as it was.
//! Lengths are integers, so "improving" means a change below zero (Python's
//! `EPSILON` is gone).
//!
//! Operators are generic over the distance storage, the candidate cache
//! mode and the tour layout, so every combination is monomorphised.

mod kicks;
mod lk;
mod or_opt;
mod random;
mod recreate;
mod scan;
mod segment;
mod three_opt;
mod two_opt;

pub use kicks::double_bridge_move;
pub use two_opt::{descend, improve_city};

use crate::City;
use crate::instance::neighbours::CandidateLists;
use crate::instance::storage::Dist;
use crate::rng::Stream;
use crate::tour::{Layout, Tour};

/// Counted work of one or more applications (`ideas/rust-port.md` §6.2).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Work {
    /// Distances read from the storage (matrix or computed).
    pub matrix_reads: u64,
    /// Distances read from the candidate cache.
    pub cache_reads: u64,
    /// Storage positions rewritten by tour changes.
    pub positions: u64,
    /// Path reversals (2-opt flips) applied, including undone ones.
    pub flips: u64,
}

impl Work {
    /// Distance reads of either kind (Python's `lookups`).
    pub fn lookups(&self) -> u64 {
        self.matrix_reads + self.cache_reads
    }

    /// Positions rewritten (Python's `moved`).
    pub fn moved(&self) -> u64 {
        self.positions
    }
}

/// The instance as the operators see it.  `CACHE` selects whether
/// (city, candidate) distances come from the candidate cache (main design)
/// or from the storage (robustness factor "cache off").
#[derive(Debug, Clone, Copy)]
pub struct Problem<'a, D: Dist, const CACHE: bool> {
    /// Distance storage.
    pub dist: &'a D,
    /// The 10 nearest neighbours of every city.
    pub neighbours: &'a CandidateLists,
    /// The 5 alpha-nearest candidates, if computed.
    pub alpha: Option<&'a CandidateLists>,
}

impl<'a, D: Dist, const CACHE: bool> Problem<'a, D, CACHE> {
    /// Number of cities.
    #[inline(always)]
    pub fn n(&self) -> usize {
        self.dist.n()
    }

    /// Distance between `i` and `j`, read from the storage.
    #[inline(always)]
    pub fn d(&self, work: &mut Work, i: City, j: City) -> i64 {
        work.matrix_reads += 1;
        i64::from(self.dist.d(i, j))
    }

    /// Candidate `slot` of `city` in `lists` and its distance, from the
    /// cache or, with the cache off, from the storage.
    #[inline(always)]
    pub fn candidate(
        &self,
        work: &mut Work,
        lists: &CandidateLists,
        city: City,
        slot: usize,
    ) -> (City, i64) {
        let candidate = lists.cities(city)[slot];
        if CACHE {
            work.cache_reads += 1;
            (candidate, i64::from(lists.distances(city)[slot]))
        } else {
            (candidate, self.d(work, city, candidate))
        }
    }

    /// The alpha candidate lists.
    ///
    /// # Panics
    ///
    /// If none are attached; experiments check [`Op::available`] for every
    /// operator before the first application.
    #[inline(always)]
    pub fn alpha_lists(&self) -> &'a CandidateLists {
        self.alpha
            .expect("alpha candidates are attached (checked by Op::available)")
    }
}

/// Outcome of one application.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Application {
    /// Length change: negative if the tour improved, else zero.
    pub delta: i64,
    /// The anchor the move was centred on.
    pub anchor: City,
}

/// Reverses the forward path `a .. b`, counting the work.
#[inline(always)]
fn reverse<L: Layout>(tour: &mut Tour<L>, work: &mut Work, a: City, b: City) {
    work.flips += 1;
    work.positions += tour.reverse(a, b);
}

/// The 2-opt move removing (x1, x2) and (y1, y2) and adding (x1, y1) and
/// (x2, y2), where `x2` follows `x1` and `y2` follows `y1` in the same
/// direction of travel (either direction): `aos.operators.exchange`.
#[inline(always)]
fn exchange<L: Layout>(
    tour: &mut Tour<L>,
    work: &mut Work,
    x1: City,
    x2: City,
    y1: City,
    y2: City,
) {
    let forward = tour.next(x1) == x2;
    debug_assert_eq!(
        tour.succ_in(y1, forward),
        y2,
        "y2 must follow y1 like x2 follows x1"
    );
    if forward {
        reverse(tour, work, x2, y1);
    } else {
        reverse(tour, work, y1, x2);
    }
}

/// `anchor`, or a uniformly random city when absent.
#[inline(always)]
fn pick_anchor(n: usize, rng: &mut Stream, anchor: Option<City>) -> City {
    anchor.unwrap_or_else(|| rng.index(n) as City)
}

/// The operators, in protocol order (O1 is `Swap`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Op {
    /// O1: exchange the anchor with a random other city.
    Swap,
    /// O2: move the anchor to a random edge.
    Insertion,
    /// O3: reverse a random segment from the anchor.
    Inversion,
    /// O4: move 1-3 cities at the anchor to a random edge.
    OrOpt,
    /// O5: first improving 2-opt move at the anchor, full scan.
    TwoOptFirst,
    /// O6: best improving 2-opt move at the anchor, full scan.
    TwoOptBest,
    /// O7: first improving 2-opt move at the anchor, neighbour list.
    TwoOptNl,
    /// O8: segment exchange at the anchor's edge, then O7 descent.
    Kick,
    /// O9: first improving Or-opt move at the anchor, neighbour list.
    OrOptNl,
    /// O10: first improving sequential 3-opt move at the anchor.
    ThreeOptNl,
    /// O11: radial ruin and greedy recreate.
    RuinRecreate,
    /// O12: double bridge at the anchor and 3 random cuts, then O7.
    DoubleBridge,
    /// O13: double bridge at the anchor and 3 neighbours, then O7.
    DoubleBridgeLocal,
    /// O14: O7 descent with a work queue, started at the anchor.
    TwoOptDescent,
    /// O15: O7 at the worst-edge city of a tournament of 8.
    TwoOptTargeted,
    /// O16: Lin-Kernighan step of up to 25 chained 2-opt moves.
    LinKernighan,
    /// O17: O7 over the 5 alpha-nearness candidates.
    TwoOptAlpha,
    /// O18: O16 over the 5 alpha-nearness candidates.
    LinKernighanAlpha,
    /// O19: SISR string removal and blink recreate.
    StringRemoval,
    /// O20: exchange 1-3-city segments at the anchor and a neighbour.
    SegmentExchange,
    /// O21: O8's segment exchange repaired by an LK descent (chained LK).
    LkKick,
}

impl Op {
    /// Every operator in protocol order.
    pub const ALL: [Op; 21] = [
        Op::Swap,
        Op::Insertion,
        Op::Inversion,
        Op::OrOpt,
        Op::TwoOptFirst,
        Op::TwoOptBest,
        Op::TwoOptNl,
        Op::Kick,
        Op::OrOptNl,
        Op::ThreeOptNl,
        Op::RuinRecreate,
        Op::DoubleBridge,
        Op::DoubleBridgeLocal,
        Op::TwoOptDescent,
        Op::TwoOptTargeted,
        Op::LinKernighan,
        Op::TwoOptAlpha,
        Op::LinKernighanAlpha,
        Op::StringRemoval,
        Op::SegmentExchange,
        Op::LkKick,
    ];

    /// The name used in `aos/operators.py` and in every output table.
    pub fn name(self) -> &'static str {
        match self {
            Op::Swap => "swap",
            Op::Insertion => "insertion",
            Op::Inversion => "inversion",
            Op::OrOpt => "or_opt",
            Op::TwoOptFirst => "two_opt_first",
            Op::TwoOptBest => "two_opt_best",
            Op::TwoOptNl => "two_opt_nl",
            Op::Kick => "kick",
            Op::OrOptNl => "or_opt_nl",
            Op::ThreeOptNl => "three_opt_nl",
            Op::RuinRecreate => "ruin_recreate",
            Op::DoubleBridge => "double_bridge",
            Op::DoubleBridgeLocal => "double_bridge_local",
            Op::TwoOptDescent => "two_opt_descent",
            Op::TwoOptTargeted => "two_opt_targeted",
            Op::LinKernighan => "lin_kernighan",
            Op::TwoOptAlpha => "two_opt_alpha",
            Op::LinKernighanAlpha => "lin_kernighan_alpha",
            Op::StringRemoval => "string_removal",
            Op::SegmentExchange => "segment_exchange",
            Op::LkKick => "lk_kick",
        }
    }

    /// The operator called `name`.
    pub fn from_name(name: &str) -> Option<Op> {
        Op::ALL.into_iter().find(|op| op.name() == name)
    }

    /// Protocol identifier, `O1` to `O21`.
    pub fn id(self) -> String {
        format!("O{}", self as usize + 1)
    }

    /// Whether the application is fully determined by the anchor
    /// (`aos.operators.DETERMINISTIC`).
    pub fn deterministic(self) -> bool {
        matches!(
            self,
            Op::TwoOptFirst
                | Op::TwoOptBest
                | Op::TwoOptNl
                | Op::OrOptNl
                | Op::ThreeOptNl
                | Op::TwoOptDescent
                | Op::LinKernighan
                | Op::TwoOptAlpha
                | Op::LinKernighanAlpha
                | Op::SegmentExchange
        )
    }

    /// Operator family (`aos.operators.FAMILIES`; O21 joins the kicks).
    pub fn family(self) -> &'static str {
        match self {
            Op::TwoOptFirst
            | Op::TwoOptBest
            | Op::TwoOptNl
            | Op::TwoOptDescent
            | Op::TwoOptTargeted => "two_opt",
            Op::Kick | Op::DoubleBridge | Op::DoubleBridgeLocal | Op::LkKick => "kick",
            other => other.name(),
        }
    }

    /// Whether the operator can run on `problem` (O17 and O18 need alpha
    /// candidates; O21 falls back to O16 without them).
    pub fn available<D: Dist, const CACHE: bool>(self, problem: &Problem<'_, D, CACHE>) -> bool {
        !matches!(self, Op::TwoOptAlpha | Op::LinKernighanAlpha) || problem.alpha.is_some()
    }

    /// Applies the operator once (see the module documentation).
    pub fn apply<D: Dist, const CACHE: bool, L: Layout>(
        self,
        tour: &mut Tour<L>,
        problem: &Problem<'_, D, CACHE>,
        rng: &mut Stream,
        work: &mut Work,
        anchor: Option<City>,
    ) -> Application {
        match self {
            Op::Swap => random::swap(tour, problem, rng, work, anchor),
            Op::Insertion => random::insertion(tour, problem, rng, work, anchor),
            Op::Inversion => random::inversion(tour, problem, rng, work, anchor),
            Op::OrOpt => random::or_opt(tour, problem, rng, work, anchor),
            Op::TwoOptFirst => scan::two_opt_scan(tour, problem, rng, work, anchor, false),
            Op::TwoOptBest => scan::two_opt_scan(tour, problem, rng, work, anchor, true),
            Op::TwoOptNl => {
                two_opt::two_opt_nl(tour, problem, rng, work, anchor, problem.neighbours)
            }
            Op::Kick => kicks::kick(tour, problem, rng, work, anchor),
            Op::OrOptNl => or_opt::or_opt_nl(tour, problem, rng, work, anchor),
            Op::ThreeOptNl => three_opt::three_opt_nl(tour, problem, rng, work, anchor),
            Op::RuinRecreate => recreate::ruin_recreate(tour, problem, rng, work, anchor),
            Op::DoubleBridge => kicks::double_bridge(tour, problem, rng, work, anchor),
            Op::DoubleBridgeLocal => kicks::double_bridge_local(tour, problem, rng, work, anchor),
            Op::TwoOptDescent => two_opt::two_opt_descent(tour, problem, rng, work, anchor),
            Op::TwoOptTargeted => two_opt::two_opt_targeted(tour, problem, rng, work, anchor),
            Op::LinKernighan => {
                lk::lin_kernighan(tour, problem, rng, work, anchor, problem.neighbours)
            }
            Op::TwoOptAlpha => {
                two_opt::two_opt_nl(tour, problem, rng, work, anchor, problem.alpha_lists())
            }
            Op::LinKernighanAlpha => {
                lk::lin_kernighan(tour, problem, rng, work, anchor, problem.alpha_lists())
            }
            Op::StringRemoval => recreate::string_removal(tour, problem, rng, work, anchor),
            Op::SegmentExchange => segment::segment_exchange(tour, problem, rng, work, anchor),
            Op::LkKick => kicks::lk_kick(tour, problem, rng, work, anchor),
        }
    }
}
