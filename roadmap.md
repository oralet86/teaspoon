# Roadmap

Written 2026-11-01. The plan of record for the move to Rust and the work
after it. Details of each phase are in
[`ideas/rust-port.md`](ideas/rust-port.md) §12; claims are in
[`ideas/claims.md`](ideas/claims.md).

## Policy: the Python code is retired

Decided 2026-11-01.

- **From now on every experiment runs in Rust** (`rust/`). The Python
  package `aos/` is no longer developed: no new features, no fixes, no
  tests. It is kept only as a **reference** that the port is checked
  against, and it is **deleted once the port is complete** (step 3 below).
- Runs already in the journal were made with the Python code. Their
  entries stay as they are; their conclusions are re-checked by the Rust
  measurements on their own, not by fixing the Python code and (since
  2026-11-05) not by a comparison with the Python run.
- Known Python defects are recorded, not fixed: the GEO distances
  (`PI = numpy.pi`, spec §4.1), the per-application timing bias found by
  E1R's calibration, the coarse 2-opt classes and the E1R policy stall.
  The Rust versions must not inherit them.

### Before `aos/` can be deleted

1. P0–P6 accepted (table below), so nothing still needs the Python
   pipeline.
2. ~~The reference data the Rust tests compare against is frozen as files in
   the repository, so the tests no longer need Python.~~ Not needed: on
   2026-11-05 the user decided that the port is no longer cross-checked
   against Python (the last run, that day, passed). **Done 2026-11-05:**
   `tests/reference.rs`, `deterministic_gains_match_python`, the `verify`
   subcommand (`src/instance/reference.rs`), the Python input of
   `compare` and `rust/tools/export_reference.py` are removed. The
   reference data stays in `data/reference-python/` (untracked) until
   `aos/` goes. The LKH alpha and Concorde flip checks do not involve
   Python and stay.
3. ~~Every script under `rust/tools/` that imports `aos` is either removed
   or rewritten.~~ **Done 2026-11-05:** none imports `aos` any more.
4. The user confirms the deletion (it is not done automatically).

Remaining for the deletion: step 1 (P2–P6 accepted, below) and step 4.

Open question: does the retirement also cover the other Python
directories, `generators/` (instance generators), `solvers/` and `bench/`
(LKH-3, Concorde, HGS and PyVRP wrappers), and the figure scripts in
`aos/analysis/`? The spec (§1) kept figures in Python reading Parquet.
Until this is decided, the Rust tool writes its own summary tables, so no
result depends on the Python analysis.

## Phases of the port

| Phase | Contents | Status |
|---|---|---|
| P0 | workspace, instances, distances, candidate lists, alpha, clock, pinning, machine checks, provenance, Parquet | **accepted** |
| P1 | both tour representations, O1–O21, work counters, deviations table | **accepted 2026-11-01** (see below) |
| P2 | E0-rs: clock, costs, contention gate, distance-read cost, `n_split`, O7 descents | run 2026-11-02, repeatability gate **not passed** (P95 CV 3.7%, 1 cell failed, 111 unresolved); rerun with the full lock to do |
| P3 | states (built and imported), measurement A, analysis of its tables | measurement A run 2026-11-02/03 (1,280 states); the §11.3 comparison with Python dropped 2026-11-05; Claims 1.5/1.6 tables to do |
| P4 | robustness factors (storage, cache, representation) | run 2026-11-02/03: leaders change in 27.7% (computed distances), 4.8% (no cache), 21.5% (two-level); acceptance pending |
| P5 | population runs and arms; exact count-mode replay | replay check run 2026-11-02/03; full runs to do |
| P6 | E1R rollouts (Claim 1.4) | D1 decided (`skip`); runs 2026-11-02/03 (top 5) and 2026-11-05 (every candidate, three slice lengths); R2's cost model to fix (journal) |

**P1 acceptance (2026-11-01).** `cargo test --release` passes (19 tests),
`clippy -D warnings` is clean, and the four cross-checks that need
external inputs pass (`cargo test --release -- --ignored`):

- reversals against Concorde's two-level flipper: identical;
- the double bridge against Concorde's four-swap: identical;
- alpha candidates with LKH's penalties: LKH's set is contained in ours for
  97.0–100% of cities on 6 instances;
- deterministic gains against Python on 30 E1 states: 100% equal for every
  deterministic operator except O14 (89.3%, deviation D2);
- distances and neighbour lists against Python on all 60 E1 instances:
  identical (GEO as decided in spec §4.1).

Not yet covered: the injection checks of spec §11.2 exist only for the
double bridge. They are a statistical check, not a gate, and are added
with P3.

## Work after the port, by claim

### Claim 1 (operator rates per state)

- E1 rerun in Rust (P3): done 2026-11-02/03; the §11.3 comparison with the
  Python run was dropped on 2026-11-05.
- E1 journal caveats, now to be checked in the Rust rerun rather than
  patched in Python: classes are too coarse (O5 alone makes a state
  "2-opt open"); back-to-back timing overcharged cheap operators by
  1.25–1.47×.
- Claim 1.5 (leader per second ≠ leader per application) and Claim 1.6
  (local optimality depends on the candidate set): their tables and
  figures computed from the Rust measurement A.
- Claim 1.4, E1R in Rust (P6): first verdicts in the journal entry of
  2026-11-05; open: R2's calibration, questions 1 and 3, R3 at more
  states per class.
- Full runs E2–E5 ([`claim-1-plan.md`](ideas/claim-1-plan.md)).

### Claim 5 (learned candidate selector per level)

- The oracle bound first: O7 handed O6's partner at each anchor, compared
  with alpha and quadrant lists at equal time
  ([`learned-candidates.md`](ideas/learned-candidates.md)). Needs the
  partner per anchor recorded, which measurement A in Rust can do.
- Literature still to search: granular neighbourhoods, POPMUSIC
  candidates, DIMES/DIFUSCO/NeuOpt, learned don't-look bits.

## Decisions waiting on the user

| # | Decision | Recommendation |
|---|---|---|
| ~~D1~~ | ~~E1R policy stall~~ | **Decided:** stall rule `skip`; `none` keeps greedy "beaten" by a stalling continuation (30% against 13% at 64 slices, 2026-11-02/03) |
| D2 | Does the retirement cover `generators/`, `solvers/`, `bench/` and the figure scripts? | retire `aos/` only; decide the rest when P3 needs figures |
| D3 | Commit the work of 2026-10-27 to 2026-11-01 (notes, journal, E1R Python modules, `rust/`) | commit before the first timed Rust run, so its code digest is clean |

## Changes of 2026-11-05

- **Five operators retired:** O2, O3, O4, O6 and O20 are removed from
  `rust/` (rule and figures in
  [`ideas/e1r-overnight-plan.md`](ideas/e1r-overnight-plan.md)). The other
  16 keep their protocol numbers.
- **No more Python cross-checks** (decision of the user). The 2026-11-05
  run of the deterministic-gains and reference checks passed on the 16
  operators, with the P1 figures (O14 at 89.3%, the rest 100%).
- **E1R analysis:** Holm's test, ties for the best candidate resolved
  towards greedy and then plan order (not the alphabet), seeds in the
  tables, `--max-candidates`, nested state draws and
  `calibrate --cost-models-from`.
- **`.gitignore`:** the rule `experiments` also hid `rust/src/experiments/`,
  so that directory has never been committed; the rule is now
  `/experiments`.

## Status at shutdown (2026-11-01)

- All of P2–P6 is now ported as `aos` subcommands: `e0`, `states`,
  `measure` (gains / times / summarise; the robustness factors are its
  `--storage`, `--no-cache` and `--layout` options), `e1r` (plan /
  calibrate / run / validate / analyse; `--stall-rule none|skip` for D1),
  `population` (fixed, uniform, pm, ap, greedy, softmax arms;
  `--check-replay`), `compare` (factor tables, Python `cells.csv`) and
  `show`. Clippy is clean and the tests pass. Every stage has been checked
  end to end on small, unlocked test data only.
- Not yet run for real: E0-rs needs boost off
  (`echo 0 | sudo tee /sys/devices/system/cpu/cpufreq/boost`).
- An estimate of the re-run time was in progress (a pilot of the gains
  stage on seed 0, stopped at 162 of about 640 states); it still needs
  doing.
- Nothing has been committed yet.
- **Timing revised (2026-11-01, later the same day)** after a review of
  how small operations are timed (spec §7.1, §7.2, §7.4, §8.1, §8.3–8.5):
  measurement A now times each operator over all anchors back to back
  (no longer interleaved with every operator) and takes its time from a
  batched pass with the restore measured and removed; the clock subtracts
  the mean overhead, not the median (the TSC moves in 10 ns steps); E0
  gains a per-call against batched check and replays flips as one region;
  population slices check their deadline about once per microsecond;
  E1R calibrates on whole slices; the core frequency over each state is
  checked with the core-cycle counter.  The E1 caveat "back-to-back timing
  overcharged cheap operators" is what the new order and the batched time
  address; the Rust rerun is where to confirm it.
- **E0 on the idle machine (night of 2026-11-02)** failed its gates with
  the clock locked; the timing code was tightened (warm-up, spin-up,
  sampled batches, retakes of disturbed samples, fixed E0 orders,
  `Tour::normalize`, `mallopt`; spec §7.2, §7.4, §8.1), which brought the
  median spread from 8.5% to 1.1–1.2% (gate 1.5%) but left the 95th
  percentile at 6.6% (gate 5%) and the drift check at 8.7%.  What remains
  is the APU's fabric and memory clocks following load and SMT siblings;
  both need root, so `lock-clock.sh` now also forces the amdgpu power
  level to `high` and turns SMT off.  E0 has not yet run with them.

