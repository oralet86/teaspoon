# Rust port: specification

Status: P0 and P1 built and accepted (2026-11-01), P2 in progress; see
[roadmap.md](../roadmap.md), which also records that the Python `aos/`
package is retired (reference only, deleted after the port). Written 2026-10-17. Decisions taken with the
user are marked **[D]** (2026-10-17, and 2026-10-31 for the defaults of
section 13, the ATT format and E1R).

Supersedes the "Implementation" paragraph under the ground rule in
[claims.md](claims.md) ("called from Python for orchestration and
analysis"): the experiments now run entirely in Rust, and Python only reads
finished run directories.

## Contents

1. [Purpose and scope](#1-purpose-and-scope)
2. [Requirements language](#2-requirements-language)
3. [Architecture](#3-architecture)
4. [Instances and distances](#4-instances-and-distances)
5. [Tour representations](#5-tour-representations)
6. [Operators](#6-operators)
7. [Timing](#7-timing)
8. [Experiments](#8-experiments) (8.5: E1R rollouts)
9. [Robustness factors (D)](#9-robustness-factors-d)
10. [Outputs and provenance](#10-outputs-and-provenance)
11. [Verification](#11-verification)
12. [Phases and acceptance](#12-phases-and-acceptance)
13. [Open decisions](#13-open-decisions)
14. [Dependencies](#14-dependencies)

## 1. Purpose and scope

**Why.** The Python implementation makes operator costs depend on the
interpreter: every distance read costs about the same 50 ns, array
rebuilds dominate on large instances, and measurement A with the planned
LK-repaired kick would take about 8.5 h (estimate of 2026-10-17). The
ground rule ([claims.md](claims.md)) asks for the best implementation of
every operator; the port provides it and makes full measurements affordable.

**In scope [D].** Everything that produces data:

- instance loading, distances, neighbour and alpha candidate lists;
- tours (two representations) and operators O1–O20 plus the new O21;
- E0 (clock, costs, contention gate, distance-read costs);
- the E1 state builder and measurement A;
- population runs (needed for E3 and the greedy-vs-softmax pilot,
  [softmax-policy.md](softmax-policy.md));
- rates, bootstrap intervals and every summary that `aos.analysis` now
  computes from raw samples, written as tables.

**Out of scope.** Figures and journal tables stay in Python
(`aos/analysis/`), reading the Rust run directories as files; no Python–Rust
calls, no FFI [D]. The solver wrappers in
`solvers/` and `bench/` are untouched. The Python `aos/` package is kept,
frozen, as the reference implementation of the runs already in the journal.

**Fidelity [D].** The port is a free reimplementation, not a transcription:
each operator keeps its *move definition and acceptance rule* (section 6)
and gets the best implementation we can give it. Equality with Python is
not required; every deliberate deviation is listed in a deviations table
kept next to the code (section 6.4).

## 2. Requirements language

MUST, SHOULD and MAY as in RFC 2119. Every MUST is checked by a test or by
a run-time check that stops the run (loud errors, never silent fallbacks).

## 3. Architecture

### 3.1 Layout

A Cargo workspace in the repository (directory name: section 13, O1) with
a library and one binary:

| Unit | Contents |
|---|---|
| `instance` | TSPLIB parsing, distance storage, candidate lists, alpha ascent |
| `tour` | the `Tour` interface, array and two-level list representations |
| `ops` | operators O1–O21, work counters |
| `timing` | TSC clock, calibration, pinning, machine checks |
| `experiments` | E0, states, A, B, population runs, robustness factors |
| `stats` | rates, bootstrap, adaptive stopping |
| `io` | run directories, Parquet tables, JSON config and provenance |
| binary | one command-line tool, a subcommand per experiment |

### 3.2 General rules

- MUST compile without warnings under `clippy -D warnings` and be
  formatted by `rustfmt`; both are run after every change, as with ruff
  and pyright on the Python side.
- MUST NOT use `unsafe` outside `timing` (TSC reads, affinity) unless a
  benchmark shows a measurable gain; every `unsafe` block carries a
  comment stating its invariant.
- Library code MUST return `Result` for every failure caused by input,
  files or the machine; panics are reserved for violated internal
  invariants. A failed state stops the run with a message naming the state.
- Operators MUST be static (generic over the tour and distance types,
  monomorphised); no dynamic dispatch on the hot path.
- Release profile: `opt-level = 3`, `lto = "fat"`, `codegen-units = 1`.
  Target CPU: section 13, O2. The profile and target are recorded in every
  run's provenance.
- All randomness MUST come from seeded generators (section 7.5); nothing
  reads the system time or OS entropy except the clock.

## 4. Instances and distances

### 4.1 Formats

MUST read the TSPLIB variants used by the E1 main run
([instances-and-operators.md](instances-and-operators.md) §1) and by the
candidate shortlist of [instance-sets.md](instance-sets.md), and MUST reject
every other variant with an error naming it:

| Type | Formats | Instances |
|---|---|---|
| `EUC_2D` | coordinates | kroA100, a280, pcb442, pr1002, pr2392, rl11849, xql662, uy734, generated Euclidean sets |
| `ATT` | coordinates (pseudo-Euclidean) | att532 |
| `GEO` | coordinates (`FUNCTION`) | gr96, gr202, ali535, gr431, gr666 |
| `EXPLICIT` | `FULL_MATRIX`, `UPPER_ROW`, `UPPER_DIAG_ROW`, `LOWER_DIAG_ROW` | random matrices, brg180, si535, pa561 |

Distances MUST follow the TSPLIB definitions exactly (`nint` for
`EUC_2D`; the GEO formula with `RRR = 6378.388`). Known discrepancy to
resolve first: the TSPLIB reference GEO code uses `PI = 3.141592`, while
`instances/tsplib.py` uses `numpy.pi`. Before P0 is accepted, check
whether this changes any gr202/gr431/gr666 distance; if it does, the
Rust loader follows TSPLIB (the listed optima assume it) and the Python
loader gets a bug report. *Checked 2026-10-31:* it does. With
`PI = 3.141592`, 4 (gr96), 7 (gr202), 64 (gr431), 258 (gr666) and 105
(ali535) distances differ from `numpy.pi`, each by 1. The TSPLIB optimal
tours of gr96, gr120, gr202 and gr666 have their listed length under both
values. The Rust loader uses `PI = 3.141592` **[D]**. Python's GEO
matrices, and with them the E1 main run's GEO state lengths, are off by
these entries (bug in `instances/tsplib.py::_degrees_to_radians`, left
unfixed so the frozen Python runs stay reproducible); imported GEO states
get their lengths recomputed. Every distance is an
integer; the tool MUST stop on a non-integral or asymmetric matrix.
Distances are stored as `i32`, tour lengths and gains as `i64`, so there
is no floating-point tolerance (Python's `EPSILON` goes away).

Optima come from the same `solution.txt` lists the Python code reads.

### 4.2 Storage: main design **[D: option A + C]**

Distance storage changes the cost of a distance read by up to 100×
(cache-resident matrix ≈ 1–4 ns, matrix in DRAM ≈ 80–100 ns, computed GEO
≈ 50–100 ns). If storage differed between instance types, Claim 1.2's
comparison of Euclidean instances and random matrices would also compare
storage schemes. The main design therefore uses **one storage scheme for
every instance**:

- **A. Full matrix.** Every instance, whatever its type, is expanded once
  into a dense row-major `n × n` `i32` matrix (rl11849: about 560 MB).
  At equal n, every instance type and every operator pays the same per
  read.
- **C. Candidate distance cache.** For every city, its candidate lists
  (10 nearest neighbours; 5 alpha-nearest when computed) are stored with
  the distance to each candidate, contiguous in memory. Operators MUST read
  (city, candidate) distances from this cache and all other distances from
  the matrix. The work counters record the two kinds of reads separately.

Known, accepted limitation: within the main design, the cost of a matrix
read still grows with n as the matrix leaves the caches. This is the same
for all instance types at a given n and is measured by E0 (section 8.1) so
that size trends can be read against it.

Storage modes `computed` (coordinates only) and `no-cache` MUST exist as
run options for the robustness factors (section 9); they MUST give
identical distances, so every deterministic result is identical across
modes and only times differ.

### 4.3 Candidate lists

- Neighbour lists: the 10 nearest other cities, ties broken by index
  (stable), as in Python.
- Alpha candidates (Helsgaun 2000): subgradient ascent on the 1-tree bound
  (LKH-like defaults, at most 300 iterations, initial step 1), then the 5
  alpha-nearest cities per city, **ordered by distance** so that the
  neighbour-list stopping rule stays valid. The computation SHOULD use
  LKH's O(n) memory scheme so that rl11849 is feasible without an
  `n × n` alpha table. Results are cached on disk with the instance hash,
  ascent settings and code version; a cache whose key does not match MUST
  be recomputed, never silently reused.

## 5. Tour representations

**[D: both from the start.]**

### 5.1 Interface

One `Tour` trait that every operator uses:

- `next(c)`, `prev(c)`, `between(a, b, c)` (is b on the path from a to c),
  `sequence` queries for the 2-opt, Or-opt and 3-opt tests;
- `flip(a, b, c, d)`: the 2-opt move replacing (a, b), (c, d) by (a, c),
  (b, d), the only primitive that changes the tour;
- `length()`, kept incrementally; `check()` recomputes length and
  validity (debug builds after every applied move, release builds after
  every state);
- `snapshot()` / `restore()` and an undo log of flips, for rollback
  (LK, trial operators) and for restoring the state between applications
  in measurement A.

Every multi-edge move (Or-opt, 3-opt, segment exchange, double bridge,
the kicks) MUST be expressed as a sequence of flips, as in LKH, so that
both representations share one operator implementation. A representation
MAY offer a faster specialised path for a move if it gives the identical
tour; that is a deviation to be listed (6.4).

### 5.2 Representations

- **Array:** `order` and `pos` arrays; a flip reverses the shorter side of
  the tour.
- **Two-level doubly-linked list** (Fredman et al. 1995, as in LKH):
  segments of about √n cities with reversal bits; flip cost O(√n).

### 5.3 Which representation a run uses

One representation per instance, chosen by a size threshold `n_split`
(section 13, O3) fixed **before** any measurement from the E0 cost curves
and recorded in provenance. All operators on an instance use the same
representation, so comparisons at equal n are unaffected. Because the
threshold can create a jump in cost ratios at `n_split`, the
representation is also a robustness factor (section 9).

Both representations MUST produce identical tours under identical flip
sequences (tested, section 11).

## 6. Operators

### 6.1 Set

**Retired 2026-11-05:** O2, O3, O4, O6 and O20 are removed from the Rust
set ([instances-and-operators.md](instances-and-operators.md) §5). The
other operators keep their identifiers: `Op`'s discriminants are fixed to
the protocol numbers, `Op::index` gives the position in `Op::ALL`, and
tables of earlier runs that still name a retired operator are read with
`Op::parse_stored`, which skips those rows.

O1–O20 keep their identifiers, names, families, determinism flags and
parameters from `aos/operators.py` (described in
[instances-and-operators.md](instances-and-operators.md) §5):

| Parameter | Value |
|---|---|
| neighbour list | 10 nearest |
| alpha candidates | 5 |
| Or-opt segment | 1–3 cities, both orientations |
| ruin size (O11) | 10 |
| tournament (O15) | 8 |
| LK depth / breadth (O16, O18) | 25 / (4, 3, 3, 2, 1) |
| SISR (O19) | L_max 10, k_s^max = 4·10/(1+10) − 1, blink 0.01 |
| segment exchange (O20) | 1–3-city segments |

**O21 (new): LK-repaired kick** (chained LK, Applegate et al. 2003; LKH's
kicks): a perturbation followed by an LK descent with a work queue started
from the cities the perturbation touched, using the LK step of O18 (alpha
candidates) or O16 when no alpha lists exist; keep the result only if
shorter. Which perturbation: section 13, O4.

### 6.2 Contract of an application

Every operator application MUST:

- take the tour, the instance, its random stream, the work counters and
  an optional anchor;
- return the length change (≤ 0) and the anchor used;
- leave the tour unchanged unless it improved (trial operators restore it);
- count work: matrix reads, cache reads, tour positions rewritten, flips.

### 6.3 Implementation freedom

Allowed, if documented: don't-look bits and queue order inside descents,
data layout, specialised fast paths, SIMD inside one application (for
example scoring all 10 candidates at once, or the full scans of O5/O6),
tie-breaking rules. Not allowed: changing which moves an operator searches,
its acceptance rule, or its parameters (6.1) without a decision recorded
here.

### 6.4 Deviations table

A table kept with the code lists every place where the Rust operator does
something different from the Python reference, with the reason. Section
11.3 checks the table against measured differences.

## 7. Timing

### 7.1 Clock

- Time is read with the invariant TSC (`rdtsc` with fences; `rdtscp` at
  the end), converted to nanoseconds with a frequency calibrated at the
  start of every run against `CLOCK_MONOTONIC_RAW`. The tool MUST check
  that the CPU reports `constant_tsc` and `nonstop_tsc` and stop if not.
- The clock's own overhead (the **mean** of empty timed regions, leaving
  out regions longer than four times the median, which were interrupted)
  is measured per run, stored, and subtracted from every per-call time.
  *(Changed 2026-11-01: the median was used before.  On the Zen 4 machine
  the counter advances in 40-tick (10 ns) steps, so an empty region reads
  40 or 80 ticks; the median, 80, exceeds the mean, 66.9, and understated
  every per-call time by about 3.3 ns.)*  The step and the median are
  recorded with the clock.

### 7.2 Machine state **[D: frequency locked]**

Timed runs MUST run with boost disabled and the `performance` governor,
on mains power. The tool does not change machine settings itself (that
needs root); it checks them before starting and stops if they are not
met, unless started with an explicit override flag, which is recorded in
provenance and in the output's name. It logs, at start, at end and every
few minutes: governor, boost state, per-core frequencies of the cores in
use, CPU temperature, and the TSC frequency estimate. A drift of the
measured core frequency beyond a tolerance (section 13, O5) during a
state marks that state's timings as suspect in the output.
*(Added 2026-11-01:)* the average frequency over a state is measured
with the thread's core-cycle counter (`perf_event_open`: user-space
cycles over task time, a slight lower bound since the task clock also
counts kernel time) against a reference measured by each worker before
its first state; the kernel's before/after snapshots are kept as well.
A counter that cannot be opened is a machine problem (the run stops
without the override flag).  So is a frequency floor below the ceiling
(`scaling_min_freq` < `scaling_max_freq`) on a core in use: with boost off
the floor must be pinned to the ceiling (4.0 GHz on the Zen 4 machine), so
a core briefly idle between states does not start the next one slower.
The cores a run may use are those of its CPU affinity (`taskset` or a
cgroup); E0's contention phases are set with `--contention-workers` to fit
them.

*Added 2026-11-02 (E0 failed on an idle, frequency-locked machine).*
Two more settings are part of the lock (`rust/tools/lock-clock.sh`;
`aos` reports a machine problem without them):

- **amdgpu power level `high`.** On this APU the GPU driver's power
  management also sets the data-fabric (FCLK: 500, 1600 or 1960 MHz) and
  memory (MCLK) clocks, which follow load unless forced.  Memory-bound
  operators ran 10–14% faster (median, up to 31%) when other cores were
  busy, and two quiet single-core phases differed by up to 12%.
- **SMT off.**  A task on the timed core's sibling thread slows it by
  24–48% (a busy loop) and even a `pause` loop by 14–41%; an idle desktop
  keeps hundreds of short tasks a second on every logical CPU.  Untimed
  stages then have 8 logical CPUs instead of 16.

### 7.3 Pinning and parallel timed workers

- Every timed worker is a thread pinned to its own physical core; the
  SMT sibling of each used core MUST stay idle. The main thread is pinned
  to a core that does no timed work.
- The number of parallel timed workers is set by the Rust E0 contention
  gate (section 8.1), not by assumption. Untimed work (the gain pass,
  alpha candidates, state building) MAY use every logical CPU but MUST NOT
  run at the same time as timed work.

### 7.4 What is timed

One application, from operator entry to exit. Restoring the state, writing
records and drawing the next anchor are outside the timed region.

*(Changed 2026-11-01, replacing the interleaved order.)*  The timed order
follows how a search uses an operator: a slice runs one operator many
times in a row at different anchors.  Per round, the operators come in a
shuffled order, and each operator runs over every anchor, in a fresh
shuffled order, back to back.  Interleaving every operator at each anchor
(the previous rule) charged cheap neighbour-list operators 1.2–1.4× their
back-to-back cost in a probe on this machine, close to the 1.21–1.47× of
the Python E1R calibration, while grouping by operator mostly stayed
within 1.00–1.08×.  Repeating one (operator, anchor) back to back stays
forbidden.  Drift is spread by the shuffled operator order of every
round.

Each (operator, round) is timed in up to three passes, in a random order
so that none always finds the caches warmed by another:

- **per call:** one timed region per application, giving each anchor's
  time (the spread over anchors, for the bootstrap and the per-anchor
  table);
- **batched:** one timed region around every application of the round,
  each restored to the state inside the region;
- **restore batch:** run only when some application of the operator
  improves.  Each improving application's tour changes are first recorded
  untimed (`Tour::replay_since`), then one timed region replays them
  (`Tour::replay`) and restores the state after each.  Replaying a change
  costs what undoing it costs, so half of this region is the restore
  time.  *(Changed 2026-11-01: an earlier version restored every
  application twice inside a second batch and took the difference of the
  two batches; that difference carried the noise of the operator's whole
  search and made cheap improving operators' times swing by several
  times their size.)*

The operator's time is the batched time per application less the
estimated restore, each the median over rounds of the round's mean (one
batch hit by an interrupt, hundreds of microseconds in a batch of tens,
made a state's mean time negative in the first locked test).  It has no clock overhead to subtract and no 10 ns
steps.  The per-call times are scaled to the batched mean for the
bootstrap, and their ratio to it is reported per state, operator and
instance.

*Added 2026-11-02.*  Every timed region of an operator's passes is now
taken under these rules (`experiments/common.rs`, `timing/disturb.rs`):

- an untimed **warm-up** pass over the anchors before an operator's timed
  passes (the first pass ran cold, the others warm: up to fourfold
  differences between repeats);
- a **spin-up** of 50 ms of busy work before each timed block (state of
  measurement A, cost block of E0, timed rollout): after the core idles,
  its caches are empty and the fabric clock may be low; an LK batch then
  ran 1.57 times its settled time and settled within 15–30 ms;
- **sampled batches**: a region shorter than 1 ms is timed at least 3
  and at most 9 times (until 1 ms in all) and the median kept;
- **retakes**: a set of samples during which the thread was preempted or
  page-faulted, or a task ran on its SMT sibling (`/proc/schedstat`), is
  taken again (at most 10 times; counted);
- the tour's storage is rebuilt from its order before each sample
  (`Tour::normalize`): undo restores the two-level list only logically;
- glibc never returns freed memory to the kernel (`mallopt`), so an
  allocation inside a timed region does not page-fault.

*Added 2026-11-02 (afternoon).*  Every timed region is also **counted**:
user-space instructions retired and core cycles of the timing thread
(`timing/counters.rs`, crate `perf-event2`, read with `rdpmc` without a
system call; columns `batch_instructions`, `batch_cycles`, `instructions`,
`cycles` per application in E0's `cost_batches` and measurement A's
batches, `total_instructions`, `total_cycles` in E0's contention rounds).
Time stays the cost.  The instruction count of an identical region does not
vary (it is unaffected by other cores), but it is no substitute for time:
over 378 (size, state, operator) cells time per instruction ranged from
0.04 to 0.21 ns, fourfold within one operator between states, and
instruction counts ordered operator pairs within 20% of each other as time
does in only about three cases in four (one unprotected time sample: nine in
ten).  The counts are a machine-independent second measure of work and a
diagnostic: cycles varying as time does locate the spread in the core's own
execution (stalls on the shared cache and memory), not in time the thread
lost to other tasks.

### 7.5 Random streams

Every application gets its own stream, derived from (run seed, state
label, operator, anchor, repeat) by a counter-based or hashed seeding
scheme. An application can therefore be replayed exactly, in any order and
on any thread: the gain pass and the timed pass (8.3) see the same random
choices. The generator and seeding scheme are named in provenance.

## 8. Experiments

Each experiment is a subcommand writing one run directory (section 10).
Settings have the same meaning as in the Python modules unless stated.

### 8.1 E0-rs

Repeats the Python E0 questions for the Rust build, plus two new ones:

1. **Clock:** overhead, resolution, TSC calibration stability.
2. **Cost per application** of every operator on random and local-optimum
   tours, n = 100 … 1000, both representations; repeatability gate:
   coefficient of variation between interleaved repeats, median ≤ 1.5%,
   95th percentile ≤ 5% (Python gate: 5% limit, median 1.3%).
3. **Contention gate:** costs with 1, 2, 4, 6, 7 timed workers (8
   physical cores, one kept for the main thread). A worker
   count passes if no operator slows down by more than 2% (median over
   repeats) against 1 worker; the largest passing count is the recommended
   worker count (section 13, O5 for the threshold).
4. **Distance-read cost** in ns per read, against n, for: matrix, cache,
   computed `EUC_2D`, computed `GEO`, random and row-sequential access.
   This is the size curve of the main design (4.2).
5. **Representation crossover:** flip and operator costs of the array and
   two-level list against n; proposes `n_split` (5.3).
6. **Full O7 descents** from random tours, per instance.
7. **Per-call against batched timing** *(added 2026-11-01)*: on O18
   optima, at anchors where no deterministic operator moves, each
   deterministic operator's mean time per application timed per call
   (interleaved with every operator, and grouped by operator) and as one
   batch; reported as ratios to the batched time.

The cost sweep (item 2) runs operators in the grouped order and the passes
of 7.4, and the repeatability gate is judged on the batched time per
application, the time measurement A's rates use; the spread of the
per-call means is reported as a diagnostic only (for operators of a few
tens of nanoseconds it is dominated by the clock's 10 ns step and single
interrupts).  Flips (item 5) are drawn first and replayed as one timed
region.

*Changed 2026-11-02:* E0's cost sweep replays one identical sequence in
every repeat: the operator order, the pass order and each operator's
anchor order are drawn per (n, layout, state), not per repeat.  Drawn per
repeat, the anchor order alone changed cheap operators' batched times at
n = 1000 by up to 2.5 times, and the two-level list's work differed
between repeats.  The contention gate's closing single-worker phase waits
until the package is within 3 °C of the opening phase (at most 180 s).

*Changed 2026-11-02 (afternoon):* each repeat of a cost block runs **three
rounds** over every operator (the first in the block's operator order, the
others in orders drawn from the block's stream: the same in every repeat),
and a repeat's batched time of an operator is the **median over its
rounds**, as measurement A takes the median over its rounds of a state.  The
per-call pass runs in the first round only.  With one round per repeat the
95th-percentile spread was 5.4–5.9%: the noisy cells repeated identical
work (instruction counts within 1%) with cycles varying as much as time and
no retakes, i.e. bursts of activity elsewhere on the chip, lasting tens of
milliseconds, slowed every sample of a set taken back to back.  Rounds
spread each operator's samples over the block; the gate now tests the
procedure measurement A uses.

*Changed 2026-11-02 (afternoon):* the contention gate's rounds time
**batches**, as measurement A does, instead of single applications: per
round and (instance, state), every operator in an order drawn per round
(the same for every worker and phase), each with an untimed warm-up pass
and one sampled batch over 32 anchors (was 16 anchors, every application
timed on its own with the operators interleaved call by call); each worker
spins up for 50 ms before its first round.  With single applications the
closing single-worker phase differed from the opening one by 2.7–4.2%
(cycles moving exactly as time, so the core ran the same work at another
speed), while the cost sweep's batched times agreed within 0.9% over the
same span.  A probe ruled out the idle cores' sleep states: spinning
threads on every other core (pause or arithmetic loops) changed batched
times on the timed core by under 0.1%.  The phases with several workers
run *faster* (cheap memory-bound operators up to 26% in cycles) because
every worker times identical jobs on the same instance, so each finds the
distance rows the others brought into the shared L3; measurement A's
workers time different states, so the gate's "no slowdown" holds a
fortiori but its speed-up is not one measurement A sees.  The instruction
and cycle overhead of counting is estimated once per process (estimated
per thread, it differed by 53 instructions between two threads).

*Changed 2026-11-02 (15:20):* timed cores are chosen **quietest first**
(`pin::physical_cores` orders cores by the interrupts each has handled
since boot); every experiment times on the first and runs its untimed
main thread on the last.  Until then the order was numeric, so CPU 0 was
both the cost sweep's timed core and the contention gate's single worker.
Without irqbalance, device interrupts stay on fixed cores (here CPU 0: the
USB controller and most timer ticks; CPU 6: the discrete GPU; CPUs 2 and
3: Wi-Fi and Ethernet).  With batched contention rounds the closing phase
still drifted 2.1%: on random-state cells at n = 1000, CPU 0 spent about
3% more user-mode cycles on identical instruction counts than every other
core through phases 0–3, then matched them in phases 4–5.  The drift was
CPU 0 changing speed, not the machine drifting.

*Changed 2026-11-02 (16:45):* the contention statistic.  Per cell
(operator, n, state) and phase, the **25th percentile** over (worker,
round) of time per application; a cell's change is that over the opening
phase's, and an operator's change the **median over its cells** (was: time
pooled over cells, then the median over (worker, round)).  The opening and
closing single-worker phases last **300 s** (was 120 s, 6 rounds per cell);
the others stay at 120 s.  With timing on CPU 4, the closing phase drifted
2.7% (pooled): the slowest cells (`two_opt_best` and `two_opt_first` at
n = 5000, about 26 µs per application) had single rounds 5–10% slow in
bursts, and pooling let them decide their operators alone.  Disturbances
only slow a round, hence a low quantile.  Recomputed on the earlier runs, the
new statistic lowered the worst contention slowdown to ≤1.3% but exposed
another effect pooling had hidden: `two_opt_descent` from 2-opt optima at
n = 5000 runs the same ~1,200 instructions in either about 116 or about
97 ns per application, holding each level for several rounds.

*Changed 2026-11-02 (17:00):* **every gate is judged per cell**, so a
steady operator can't carry an unsteady one.  Each cell gets a 95%
interval and a verdict: *pass* (interval inside the limit), *fail*
(interval wholly beyond it) or *unresolved* (straddling it: too few
samples to tell).  A gate passes only when every cell passes.
- **Contention and drift:** per (operator, n, state) and phase after the
  first, the change of the 25th percentile over (worker, round) against
  the opening phase, with a 1000-resample bootstrap interval (both phases
  resampled independently).  Phases with several workers: change ≤ 2%
  (one-sided).  Closing phase: within ±2%.
- **Repeatability:** per (n, layout, state, operator), the coefficient of
  variation between repeats, with the chi-square interval of a standard
  deviation, at most 5% (the old 95th-percentile limit, now for every
  cell).  With 5 repeats a cell passes only at a measured coefficient of
  variation ≤ 1.74% and fails above 8.3%; the median and 95th percentile
  over cells are still reported.
- Both write their cells to `repeat_cells.parquet` and
  `contention_cells.parquet`; the summary lists every cell that did not
  pass.

On runs 2 and 3 the contention rule found 3 and 6 failing cells out of
525, most of them `two_opt_descent`'s two-level effect.

### 8.2 States

The state builder of `aos/e1_states.py`: `random`, `nearest_neighbour`,
`d25`–`d90`, `opt2`, `oropt`, `opt3`, `lkopt`, `ils1`, `ils2`, with
duplicate detection. Two sources, both MUST be supported:

- **built in Rust**, with the same definitions;
- **imported** from a Python states directory (the E1 main run:
  `runs/e1-main/20261018-144422/states`), so that the Rust measurements run
  on exactly the tours the Python run used.

### 8.3 Measurement A **[D: two passes]**

*Note 2026-10-18:* the Python build now has the same two passes
(`aos/marginal.py`, `aos/e1_marginal.py`), with three differences: the
stop rule is judged on the deterministic operators only (a random
operator's rounds replay other applications, so their spread is its own
randomness), the target is 5% rather than 2% (cheap neighbour-list
operators need about 8 rounds for 2% in Python), and the garbage collector
is off while timing.

For every state, operator, anchor and repeat, as in the Python design
(32 repeats for the random operators in the main run, all anchors where
affordable, early states skipped above n = 1000 unless the budget allows
them).

**Pass 1, gain (untimed, all logical CPUs).** Every application is run
once from the state tour and records gain, improved yes/no, work counters
and, for improving applications, a hash of the resulting tour.
Deterministic operators need one application per anchor; their repeats
exist only in the timed pass. Pass 1 also computes the exact
local-optimality flags (2-opt, Or-opt, 3-opt, LK) by full enumeration.

**Pass 2, time (pinned timed workers).** The same applications are
replayed with their own random streams and timed one by one (7.4). Every
replay MUST reproduce its pass-1 gain and tour hash; a mismatch stops the
run. Timing is adaptive per (state, operator):

- rounds of timed repeats over all anchors (or a fixed stratified sample
  of anchors when all anchors do not fit the budget), each round in the
  grouped order and passes of 7.4;
- stop when, for every deterministic operator, the 95% interval of the
  mean time is within ±2% of it, or at 8 timed repeats, whichever comes
  first (section 13, O6). As in Python, only the deterministic operators
  are judged: a random operator's rounds replay other applications, so
  their spread is its own randomness, which the rate's bootstrap carries;
- a per-state time budget remains as a cap, as `state_seconds` does now.

**Rate.** Mean gain over all pass-1 applications divided by the batched
mean time of 7.4.  The precision rule above is judged on the batched round
means. Interval: bootstrap with anchors resampled
jointly for gain and time (stratified when every anchor was used, cluster
bootstrap when anchors were sampled), 1000 resamples, 95%. Reported per
cell exactly as the columns of today's `cells.csv`, plus the number of
timed applications and the timing stop reason.

### 8.4 Population runs

The steady-state population search of `aos/population.py` (tournament
selection, one slice per step, replace the worst if shorter and new), with
time slices and budgets in wall-clock time or in application counts, and
the per-slice log of today's `SliceLog`.  In time mode the slice deadline
is checked about once per microsecond (every k applications, k set from
the slice's pace so far and never past the time left), not after every
application: a fenced clock read costs about as much as a cheap
application and was charged to the operator *(changed 2026-11-01)*. Arms: fixed, uniform, replay, and
the policies E3 needs (greedy and softmax over a rate table,
[softmax-policy.md](softmax-policy.md)). Time-bounded runs log every
slice's application count so that a run can be replayed exactly in count
mode, as in Python.

### 8.5 E1R rollouts **[D: added 2026-10-31]**

The rollouts of Claim 1.4 (`aos/rollouts.py`, `aos/e1_rollouts.py`): one
slice of a candidate first operator from an E1 state, then greedy by level
(the E1 leader of the tour's exact local-optimality class) up to a
horizon, on a virtual clock. Same four stages as in Python:

- `plan`: stratified state sample, candidates (G(s), the policy's choice,
  the next operators by rate), the greedy-by-level map from the leaders
  table of measurement A, and a provisional cost model per instance and
  operator, `a + b·lookups + c·moved` ns, fitted with non-negative least
  squares on measurement-A timings and checked on a held-out seed;
- `calibrate` (one pinned core): timed rollouts, refit.  *(Changed
  2026-11-01:)* the clock is read around whole slices, and the model
  `t = a·n + b·lookups + c·moved` is fitted on slice totals (n
  applications per slice), so the applications run exactly as untimed.
  The plan's provisional fit uses measurement A's per-call times scaled to
  the batched mean of their state and operator;
- `run` (untimed, every CPU, resumable): the paired rollouts;
- `validate` (one pinned core): replays with real timing that MUST
  reproduce the stored results.

*Slice length (decided 2026-11-01).* The default is 0.01 ms per city
(`slice_ns_per_city = 10000`), not the Python design's 0.1 ms: the slice
is kept equal in work, not in time.  The port had first set 0.01 ms without
a recorded reason; it was set to 0.1 ms, tested, and set back for this
reason.  A Rust application is about 14 times faster than a Python
one, so a slice of the same length holds about 14 times the
applications: on the 500-city instances Python's 0.1 ms slices held a
median of about 1,700 applications (E1R test run, 2026-10-27), Rust's
0.01 ms slices about 2,300 and Rust's 0.1 ms slices ten times that.  With
0.1 ms the rollouts therefore search much further per slice and per
horizon than Python's did, and the run stage takes about 7 times longer
(17 pilot states: 73 s at 0.01 ms, 8 min 45 s at 0.1 ms).  The main E1R
runs use 0.01 ms; one run at 0.1 ms is a sensitivity check of whether the
verdict depends on the slice.  The slice should finally match the
decision unit of the E2/E3 population runs, which is not decided yet.

*Cost fit (2026-11-01).* The non-negative fit drops one feature at a time,
the one with the most negative contribution, and accepts a fit on the
moved count alone.  Slice totals make the features nearly collinear (a
double bridge reads the same number of distances every time, so its
lookups are a multiple of its applications); dropping every negative
coefficient at once then dropped both of a collinear pair and the fit
failed ("predicts no positive time") on `double_bridge` at 0.1 ms slices.
An application the model charges nothing is an error where it is charged
(it would otherwise never end its slice).

*Changed 2026-11-03:* the fit keeps the **intercept** in every model: the
best non-negative least-squares fit over the four feature subsets that
contain it (the intercept alone, the mean time per application, is always
feasible), and a fit without a positive intercept is an error.  Over all
seven subsets (2026-11-02), 18 of 822 (instance, operator) fits of an E1R
run put the whole cost on the moved cities (for example `two_opt_nl` on
d198: 26,900 ns per moved city, nothing otherwise; moves are rare and
costly in slice totals), so an application that moved nothing cost 0 ns
and the run stopped.  Every application has a fixed cost (the call, its
random draws, its anchor).  The Python reference refused a fit with
neither an intercept nor a per-lookup cost.

The work counters MUST therefore expose `lookups` (matrix plus cache
reads) and `moved` (tour positions rewritten). Measurement A MUST write
the leaders table with the exact class per state (the summary
`aos.analysis.e1_marginal` computes now), since `plan` reads it.

## 9. Robustness factors (D)

**[D]** Measurement A is rerun on a subset with one implementation choice
changed at a time, and every state whose leader (or leader-vs-rival
separation) changes is reported:

| Factor | Main | Alternative | Subset |
|---|---|---|---|
| Distance storage | matrix | computed from coordinates | every coordinate instance (EUC_2D and GEO) |
| Candidate cache | on | off | all instances |
| Tour representation | by `n_split` | the other one | instances with n within a factor 2 of `n_split` |

Default subset: state seeds 0 and 1, every stage (section 13, O7).
*(Added 2026-11-01:)* a factor run reuses the main run's anchors
(`measure --anchors-from <main run>`): sampled anchors are sized from an
untimed wall-clock pilot, so without it each run draws its own sample and
a leader can change with the sample rather than the implementation.  With
computed distances the gains rows are identical to the matrix's (work
counters included), so that run may copy the main run's gains; with the
cache off the counters split differently between matrix and cache reads,
and the two-level list rewrites other positions, so those recompute. The
output is a table per factor (state, main leader, alternative leader,
rate ratios of the top operators) that the Python analysis turns into a
journal section. A leader that changes under a factor is flagged in the
journal as implementation-dependent, following the existing rule in
[claims.md](claims.md).

## 10. Outputs and provenance

### 10.1 Format **[D: Parquet]**

- Tables are Parquet files (Arrow schema stated per table, written with
  the `arrow`/`parquet` crates). The Python analysis reads them with
  `pyarrow`, to be added to `pyproject.toml` when the analysis is adapted.
- Settings and provenance stay in `config.json`.
- A table is written to a temporary name and renamed when complete.
  Long runs write one part file per finished unit (for example per state)
  under `<table>.parts/` and merge the parts at the end, so an interrupted
  run keeps the finished states and resumes after them, as
  `cells.partial.csv` does now. *(Changed 2026-10-31: appending row groups
  to one file does not survive an interruption, because a Parquet file is
  unreadable until its footer is written.)*

### 10.2 Run directory

`runs/<experiment>-rs/<timestamp>/` with, for measurement A:

| File | Rows |
|---|---|
| `config.json` | settings and provenance |
| `states.parquet` | one per state: instance, seed, stage, length, flags, order |
| `gains.parquet` | one per pass-1 application |
| `timings.parquet` | one per timed application, with frequency-drift flags |
| `cells.parquet` | one per (state, operator): today's `cells.csv` columns plus the new ones |
| `nodes.parquet` | per (state, operator, anchor): summed gain and time |
| `machine.parquet` | the periodic machine-state log (7.2) |

Other experiments follow the same pattern; their table lists are part of
the phase that implements them.

### 10.3 Provenance

`config.json` MUST record: git commit and dirty flag, a digest of the Rust
sources, `Cargo.lock` hash, `rustc` version, build profile and target CPU,
host CPU model and flags, kernel, governor and boost state, TSC frequency,
clock overhead, timed-worker count and pinned cores, storage mode,
representation and `n_split`, PRNG and seeding scheme, and every setting.

## 11. Verification

### 11.1 Required tests (MUST pass before any timed run)

- **Distances:** for every instance in the list, the Rust matrix equals
  the Python loader's matrix exactly (except for a GEO difference resolved
  as in 4.1); the computed modes equal the matrix.
- **Tours:** random flip sequences give identical tours in both
  representations; `check()` holds after every flip.
- **Operator contract:** on random instances, tours and anchors: the
  returned change equals the recomputed length change, never positive, and
  a failed application leaves the tour identical.
- **Local optimality:** a tour reported as an O7, O9, O10 or LK optimum
  has no improving application at any anchor for that operator.
- **Replay:** an application replayed from its stream gives the same gain
  and tour, on any thread and in any order.
- **Cross-mode:** deterministic operators give identical gains per anchor
  across storage modes, cache on/off and representations.

### 11.2 Statistical checks

Stochastic operators: with the random choices injected directly (cut
points, ruined cities, kick positions), the result equals a direct
computation of the same move; without injection, gain distributions on
fixed states are compared against the Python reference and differences
must be explained by the deviations table.

### 11.3 Comparison with the Python reference (diagnostic, not a gate)

On the imported main-run states: per operator, the share of anchors with
the same gain as Python for deterministic operators; per state, whether
the leader and the class assignment agree. Every disagreement is traced to
an entry in the deviations table or reported as a bug. The result is a
journal entry.

## 12. Phases and acceptance

| Phase | Contents | Accepted when |
|---|---|---|
| P0 | workspace, instances, distances (all modes), candidate lists, alpha, clock, pinning, machine checks, provenance, Parquet output | distance tests pass; alpha lists computed for every instance |
| P1 | both tour representations, O1–O21, work counters, deviations table | all of 11.1 passes |
| P2 | E0-rs | gate passes; worker count, `n_split` and read-cost curves in the journal |
| P3 | states (built and imported), measurement A, Python analysis reading Parquet | A runs end to end on the imported main-run states; 11.3 written up |
| P4 | robustness factors | factor tables in the journal |
| P5 | population runs and arms | count-mode replay of a time-bounded run is exact |
| P6 | E1R rollouts (8.5); built after P3 | `validate` reproduces every replayed rollout; cost-check error in the journal |

The Python code stays in place until P3 is accepted. Nothing in `aos/` is
deleted by this port; retiring it is a separate decision.

## 13. Decisions taken 2026-10-31

The proposed defaults below were all accepted **[D]**:

| # | Decision | Proposed default |
|---|---|---|
| O1 | Workspace directory | `rust/` at the repository root |
| O2 | Target CPU | `x86-64-v3` (AVX2), not `native`, so the build is reproducible on other machines; recorded in provenance |
| O3 | `n_split` | set from E0-rs (8.1, item 5); until then 1000 |
| O4 | O21 perturbation | O8's segment exchange (keeps O21 comparable with O8); a double-bridge variant as O22 if the comparison is wanted |
| O5 | Gate thresholds | contention ≤ 2% slowdown; frequency drift ≤ 1% within a state |
| O6 | Adaptive timing target | ±2% (95% interval of mean time), at most 8 timed repeats |
| O7 | Robustness subset | state seeds 0 and 1, all stages |
| O8 | Early states above n = 1000 | measured if the budget allows, else skipped as now |

## 14. Dependencies

**[D: free choice, listed.]** Crates, pinned in `Cargo.lock`:

| Crate | Use | Why not hand-written |
|---|---|---|
| `rand_core`, `rand_xoshiro` (or `rand_pcg`) | seeded, splittable random streams | well-tested generators with stated properties |
| `serde`, `serde_json` | `config.json`, importing Python states | standard |
| `arrow`, `parquet` | output tables | Parquet is the chosen format |
| `clap` | command line | standard |
| `core_affinity` or `libc` | pinning threads to cores | thin OS bindings |
| `sha2` | source and instance digests | provenance |
| `thiserror`, `anyhow` | error types in library and binary | loud, typed errors |
| `log`, `env_logger` | diagnostics | no bare prints |
| `proptest` (dev) | property tests of 11.1 | random instances and flip sequences |
| `perf-event2` | instruction and cycle counts, core-frequency checks (`timing/counters.rs`, `timing/cycles.rs`) | replaced a hand-written `perf_event_open` binding |
| `statrs` | mean, median, quantile, standard deviation; Student-t and chi-square quantiles | known implementations instead of hand-written ones |
| `nalgebra` | least-squares solves of E1R's cost fit (SVD) | replaced hand-written elimination |
| `rand` | weighted operator choice (`WeightedIndex`) | replaced a hand-written cumulative walk |
| `nix` | CPU affinity, `CLOCK_MONOTONIC_RAW`, `getrusage`, `sched_getcpu` | safe wrappers instead of `unsafe` `libc` calls (`libc` stays for glibc's `mallopt`) |
| `jiff` | run-directory timestamps | replaced `unsafe` `localtime_r` |
| `procfs` | `/proc/cpuinfo` | parser; `/proc/interrupts` and `/proc/schedstat` (system-wide) are not covered and stay hand-parsed |
| `rayon` | untimed parallel work (`parallel_map`) | replaced a hand-written pool |
| `csv`, `arrow` (`csv` feature) | reading the Python `cells.csv`, writing `aos show` output | correct quoting |

*Changed 2026-11-02:* hand-written mathematical, statistical and system
code replaced with the crates above (the user's rule: known
implementations over hand-written ones).  Effects on results:
- **Quantiles** follow Hyndman and Fan's definition 8 (`statrs`), not
  NumPy's definition 7: quantiles of few values shift slightly.
- **Measurement A's precision check** uses the exact Student-t quantile;
  the old table stopped at 10 degrees of freedom and reused 2.228 above
  that, so larger samples got too wide an interval and were sampled
  longer than needed.
- **E1R's cost fit** is exact non-negative least squares (best fit over
  the feature subsets that keep the intercept, since 2026-11-03, §8.5);
  the old drop-and-refit loop need not reach it.
- **Operator draws** no longer fall back to the last operator when
  rounding leaves the cumulative target unspent.

Cross-checked against scipy 2026-11-02 on fixed data: quantile, median,
coefficient of variation, chi-square and Student-t quantiles match to
rounding; the non-negative fit reaches `scipy.optimize.nnls`'s residual;
the bootstrap intervals (E1R paired, measurement A cluster, E0
contention) and the sign-flip p-value agree with `scipy.stats.bootstrap`
and `permutation_test` within resampling noise.  `sign_flip_p` returns 1
whenever the observed mean is not positive instead of the permutation
p-value (conservative; scipy gives e.g. 0.76).

Python side: `pyarrow`, added when the analysis is adapted (P3).
