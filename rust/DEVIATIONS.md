# Deviations from the Python reference

Every place where the Rust operators or tours do something different from
`aos/operators.py` and `aos/tour.py` (`ideas/rust-port.md` §6.4). Move
definitions, acceptance rules and parameters are unchanged; only how a
move is carried out differs.

| # | Where | Rust | Python | Reason | Measured effect |
|---|---|---|---|---|---|
| D1 | All operators | Lengths and gains are integers; "improving" means change < 0 | Floats with `EPSILON = 1e-9` | Every distance is an integer (§4.1) | None |
| D2 | Tours, all multi-move operators | Logical orientation: a reversal keeps the direction of the cities outside the path; the layout records a global reversal bit when it rewrites the complement | The array rewrites the shorter side and the direction of travel flips with it | Same `next`/`prev` in both layouts and exact undo | Single-move deterministic operators are identical on 30 E1 states × 100 anchors; O14 differs at 10.7% of anchors (later moves in a descent search in the other direction first) |
| D3 | Every move | Expressed as path reversals (flips) on the tour | Array rewrites: `pop`/`insert`, slicing and full rebuilds | One implementation for both layouts (§5.1) | Work counter `moved` counts storage positions rewritten by flips |
| D4 | O1 | Exchanges the two cities in place (O(1) in both layouts) | Same, through positions | Fast path that gives the identical tour (§5.1) | None |
| D5 | O8, O12, O13, O21 | Kick applied to the tour, descent in place, a rejected trial undone through the undo log | Kick built on a fresh O(n) array copy, which is dropped if rejected | Avoids the O(n) rebuild that dominated large instances (E1x) | Same logical tour after a rejection; different cost |
| D6 | O11, O19 | Accepted result replaces the order (O(n), logged for undo) | Rebuilds the array (O(n)) | Arbitrary reinsertion of up to 30 cities is not a short flip sequence | None on the tour |
| D7 | O11, O19 fallback | When no neighbour-list city of a reinserted city is in the tour, the full scan starts at the first remaining city along the tour from city 0 | From the first remaining city in array order | No array order exists in the two-level layout | Only tie-breaking among equal-cost positions |
| D8 | Descents (O8, O12-O14, O21) | Start cities deduplicated in the work queue | Duplicates (for example `x_start == x_end`) are queued twice | A city processed twice in a row cannot improve the second time unless the first did, which re-queues it anyway | None expected |
| D9 | O2, O3, O4, O12 | Random cities drawn as offsets along the tour from the anchor | Random array positions | No array positions in the two-level layout | Same distributions; different draws (another generator anyway) |
| D10 | All random operators | xoshiro256++ streams seeded per application (`rust/src/rng.rs`) | `random.Random` seeded with strings | §7.5 | Different random draws; distributions unchanged |
| D11 | Tour identity | Rotation- and orientation-free 64-bit FNV hash of the cycle | CRC32 of the array order | Orientation and rotation are arbitrary | None |
| D12 | Two-level layout | Segments in a vector with ranks and offsets, rebuilt when splits double the segment count | (No two-level layout in Python) | Simpler than LKH's linked list of parents at the same O(√n) per flip | Matches Concorde's `flip_two.c` exactly under 1,500 random flips (`tests/external.rs`) |
| D13 | Work counters | Every distance read counted where it happens, matrix and cache reads separately; flips counted | Per-operator formulas for lookups | Exact counts, and the split the main design needs (§4.2) | `lookups` differs slightly from Python's formulas |

## Cross-checks against independent implementations

`rust/tests/external.rs` (run with `--ignored` and the environment
variables it names):

- Path reversals, both layouts, against Concorde's two-level flipper
  (`LINKERN/flip_two.c`): identical `next[]` after each of 300 random flips
  at n = 8, 13, 50, 101 and 1000.
- The double bridge against Concorde's `random_four_swap`: identical cycle
  and length change in 200 trials.
- Alpha candidates against LKH-3 (`vendor/LKH`): with LKH's own penalties,
  LKH's candidate set is contained in ours for 97-100% of cities; the rest
  follow from LKH's different special 1-tree node. With our own ascent,
  83-95% of candidates agree (LKH ascends over a sparse graph without the
  300-iteration cap).
- Deterministic operators against the Python reference on 30 E1 states of
  kroA100, netgen-100-0 and portmgen-100-0: identical gains at every anchor
  for O5, O6, O7, O9, O10, O16, O17, O18 and O20; O14 at 89.3% (D2).
