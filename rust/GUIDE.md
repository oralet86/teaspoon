# A reader's guide to the Rust implementation

This guide walks through every file under `rust/`, function by function.
It is written for someone who knows the research (the claims, E0, E1,
E1R) and has read the Rust book up to chapter 4 (ownership and
borrowing), but no further. Each section says **what** a piece of code
does, **how** it does it, and **why** it exists or is written that way.

Written 2026-11-05 against the working tree of that day (commit `4f0eaca`
plus uncommitted changes). Line counts are approximate.

## Contents

0. How to read this guide
1. The Rust you need beyond chapter 4
2. The big picture
3. Build files: `Cargo.toml`, `build.rs`, toolchain
4. `src/lib.rs` and `src/main.rs`: the entry points
5. `src/rng.rs`: seeded random streams
6. `src/instance/`: problems, distances, candidate lists
7. `src/tour/`: how a tour is stored and changed
8. `src/ops/`: the operators
9. `src/timing/`: the clock and the machine
10. `src/io/`: run directories, Parquet, provenance
11. `src/experiments/`: the experiments themselves
12. `tests/`: the test files
13. `tools/`: shell scripts and the C harness
14. Where to start reading the code yourself

---

## 0. How to read this guide

- **Read sections 1 and 2 first.** Section 1 is a short Rust primer for
  everything you will meet that comes after chapter 4 of the book, with a
  pointer to the chapter that explains it properly. Section 2 is the map:
  what the program does from start to finish and which file does which
  part.
- **Then read in dependency order.** Sections 5 to 8 are the building
  blocks (random numbers, instances, tours, operators). The experiments in
  section 11 use all of them. Reading in this order means you always know
  what a called function does.
- **Keep the code open beside the guide.** Function names in the guide are
  exactly as in the code, so you can search for them (`fn name`).
- **Small helpers are grouped.** A two-line function that formats a
  number does not get its own paragraph; it gets one sentence in a list.

---

## 1. The Rust you need beyond chapter 4

You know ownership (every value has one owner, and it is dropped when the
owner goes away), borrowing (`&x` reads, `&mut x` changes, never both at
once), and slices (`&[T]`, `&str`). Here is everything else this code
uses, in the order you will meet it.

### 1.1 `struct`: a named bundle of fields (book ch. 5)

```rust
pub struct Clock {
    pub ticks_per_second: f64,
    pub overhead_ticks: u64,
}
```

A struct groups related values, like a Python dataclass. `pub` means
other files may use it. Functions that belong to a struct live in an
`impl` block:

```rust
impl Clock {
    pub fn seconds(&self, ticks: u64) -> f64 { ticks as f64 / self.ticks_per_second }
}
```

- `&self` means "a read-only borrow of the struct this is called on",
  like Python's `self`. `&mut self` means the method may change it.
  Plain `self` means the method takes ownership: the struct is used up.
- A function in an `impl` without any `self` (like `fn new() -> Self`) is
  called with `Clock::new()`. These are "associated functions", Rust's
  version of a class method or constructor. `Self` is shorthand for the
  type being implemented.

### 1.2 `enum` and `match`: one of several cases (book ch. 6)

```rust
pub enum Layout { Array(ArrayTour), TwoLevel(BlockTour) }
```

An enum value is exactly one of its variants, and a variant can carry
data. `match` looks at which variant you have and runs the matching arm;
the compiler refuses to build if a case is missing. That is why adding an
operator to the `Op` enum forces you to handle it everywhere.

Two enums are everywhere and worth knowing by heart:

- **`Option<T>`** is `Some(value)` or `None`. Rust has no `null`; a value
  that may be missing is an `Option`.
- **`Result<T, E>`** is `Ok(value)` or `Err(error)`. Functions that can
  fail return a `Result` instead of raising an exception.

`if let Some(x) = maybe { ... }` is a short `match` for one case.
`let Some(x) = maybe else { break };` means "unpack it, or else leave".

### 1.3 Errors and the `?` operator (book ch. 9)

```rust
fn load(path: &Path) -> anyhow::Result<Instance> {
    let text = std::fs::read_to_string(path)?;
    ...
}
```

The `?` after a call means: if this returned an error, return that error
from my function right now; otherwise give me the value. It replaces
Python's "let the exception propagate". `anyhow::Result<T>` is a
`Result` whose error can be any kind of error with a message; this crate
uses it almost everywhere. Related helpers you will see:

- `.context("reading the plan")?` or `.with_context(|| format!(...))?`
  adds a sentence to the error so the final message reads like a chain:
  "reading the plan: file not found".
- `anyhow::bail!("message")` returns an error immediately.
- `anyhow::ensure!(condition, "message")` returns an error if the
  condition is false (an `assert` that returns an error instead of
  crashing).
- `.expect("why this cannot fail")` takes the value out of an `Option` or
  `Result` and **crashes the program** (a "panic") if it is missing. The
  message documents why the author believes it cannot happen.
- `thiserror` (in `rng.rs`, `tsplib.rs` and others) is a small library
  that lets a file define its own error enum with readable messages.

### 1.4 Modules and `use` (book ch. 7)

Each file is a module. `pub mod tour;` in `lib.rs` says "the file
`tour/mod.rs` (or `tour.rs`) is a module called `tour`". Paths use `::`:
`crate::tour::Tour` is the `Tour` type in the `tour` module of this
crate. `use` imports a name so you can write `Tour` instead of the full
path. `super::` means "the parent module".

### 1.5 Collections (book ch. 8)

- `Vec<T>`: a growable array (Python list of one type).
- `HashMap<K, V>` and `BTreeMap<K, V>`: dictionaries. A `BTreeMap` keeps
  its keys sorted, which this code uses whenever the output order must be
  reproducible.
- `HashSet`, `BTreeSet`: sets.
- `String` is an owned, growable string; `&str` is a borrowed view into
  one.

### 1.6 Generics, traits and lifetimes (book ch. 10)

- **Generics:** `fn total<T: Distance>(d: &T)` works for any type `T`
  that satisfies the `Distance` trait. The compiler makes a separate,
  fully optimised copy for each type actually used, so there is no speed
  cost.
- **Traits** are like interfaces or Python's abstract base classes: a
  list of methods a type promises to have. `impl Distance for Matrix`
  makes the promise for `Matrix`. Some traits come from the standard
  library: `Clone` (can be copied explicitly with `.clone()`), `Copy`
  (copied implicitly, like integers), `Debug` (printable with `{:?}`),
  `Default` (has a default value), `PartialEq`/`Eq` (`==` works),
  `Ord` (can be sorted).
- `#[derive(Clone, Debug, ...)]` above a struct asks the compiler to
  write those trait implementations automatically. `Serialize` and
  `Deserialize` (from the `serde` library) are derived the same way and
  mean "can be written to and read from JSON".
- **Lifetimes** (`'a`) appear rarely here. `struct Ctx<'a> { inst: &'a
  Instance }` just says "this struct holds a borrow, so it cannot outlive
  the `Instance` it borrows". You can read them as noise at first.
- `dyn Trait` means "some type that implements this trait, decided at run
  time" (like ordinary Python method calls). Generics decide at compile
  time; `dyn` decides at run time.

### 1.7 Tests (book ch. 11)

```rust
#[cfg(test)]
mod tests {
    #[test]
    fn two_plus_two() { assert_eq!(2 + 2, 4); }
}
```

`#[cfg(test)]` means this code is only compiled for `cargo test`. Files
under `rust/tests/` are separate test programs that use the library from
outside, as a user would.

### 1.8 Closures and iterators (book ch. 13)

A closure is an unnamed function: `|x| x * 2` is Python's `lambda x: x *
2`. Iterators are chains of operations over a sequence:

```rust
let total: i64 = gains.iter().filter(|g| **g > 0).sum();
```

reads as "go through `gains`, keep the positive ones, add them up". You
will meet `.map(...)` (transform each), `.filter(...)` (keep some),
`.enumerate()` (pair each item with its index), `.zip(...)` (walk two
sequences together), `.collect()` (build a `Vec` or map from the
results), `.sum()`, `.min_by(...)`, `.max_by_key(...)`. Nothing runs
until something consumes the chain (`collect`, `sum`, a `for` loop).

`move |x| ...` makes the closure take ownership of what it uses, which is
needed when the closure runs on another thread.

### 1.9 Smart pointers and shared data (book ch. 15–16)

- `Box<T>` puts a value on the heap. It appears where a value's size is
  not known at compile time (`Box<dyn Error>`).
- `Arc<T>` lets several threads share read-only data; it is freed when
  the last thread is done with it.
- `Mutex<T>` lets several threads change shared data, one at a time:
  `.lock()` waits for your turn.
- `AtomicUsize` is an integer several threads can increase safely
  without a lock. `main.rs` and the experiments use one as a shared
  "next job" counter: each worker does `fetch_add(1)` to claim the next
  job number.
- `std::thread::scope(|scope| { scope.spawn(...) })` starts threads that
  are guaranteed to finish before `scope` returns, so they may borrow
  local variables.
- **rayon** is a library that makes iterators parallel:
  `items.par_iter().map(...)` spreads the work over every CPU.

### 1.10 `unsafe`, macros and attributes (book ch. 20)

- **`unsafe { ... }`** marks code where the compiler cannot check memory
  safety, so the programmer promises it is right. Here it is used only in
  `src/timing/` for reading the CPU's cycle counter and calling operating
  system functions. Each `unsafe` block has a `// SAFETY:` comment saying
  why it is correct.
- **Macros** end with `!`: `println!`, `format!`, `vec![...]`,
  `assert!`, `anyhow::bail!`. They are code that writes code; treat them
  as functions with flexible arguments.
- **Attributes** are the `#[...]` lines above items:
  - `#[derive(...)]` (above);
  - `#[inline]` (hint to the compiler);
  - `#[cfg(test)]` (test only);
  - `#[arg(...)]`, `#[command(...)]` (for the command-line parser);
  - `#[serde(...)]` (JSON field names).

### 1.11 Numbers

- **Integer types:**
  - `u32`, `u64`, `usize`: unsigned (never negative). `usize` is the
    machine word, used for indices and lengths.
  - `i32`, `i64`: signed.
  - Tour lengths and gains are `i64` here because every distance is an
    integer (TSPLIB rounds them).
- **Floating point:** `f64` is a 64-bit float, Python's `float`.
- **Conversions:**
  - `x as f64` converts a number explicitly. Rust never converts number
    types silently.
  - `u32::try_from(x)?` converts and fails loudly if the value does not
    fit.

### 1.12 Printing and formatting

`format!("{name}: {:.3}", value)` builds a string like Python's
f-strings. `{:?}` uses `Debug`, `{:.3}` gives three decimals.
`log::info!(...)` prints a timestamped log line; `log::warn!` a warning.

---

## 2. The big picture

### 2.1 What the program is for

The Rust code is a laboratory for one question: **which local-search
operator gains the most tour length per second, in each state of a TSP
search?** Everything in it either:

1. builds the ingredients of that question (instances, tours, the
   operators, the candidate neighbour lists), or
2. measures time very carefully (the clock, CPU pinning, machine checks),
   or
3. runs one of the experiments and writes its results to disk.

Figures and journal tables are drawn in Python from the files the Rust
program writes.

### 2.2 One binary, many subcommands

`cargo build --release` produces one program, `rust/target/release/aos`.
You run it with a subcommand, like `git commit` or `git log`:

| Subcommand | What it does | Code |
|---|---|---|
| `aos instance <files>` | Load instances and print their size and status | `main.rs` |
| `aos candidates <files>` | Compute and cache alpha-nearness candidate lists | `instance/alpha.rs` |
| `aos clock` | Calibrate the CPU clock and report whether the machine is locked for timing | `timing/` |
| `aos e0` | E0-rs: how precise and repeatable is our timing? | `experiments/e0.rs` |
| `aos states` | Build the E1 search states (tours at each local-optimality level) | `experiments/states.rs` |
| `aos measure` | Measurement A: the marginal rate of every operator in every state | `experiments/marginal.rs` |
| `aos e1r` | E1R rollouts for Claim 1.4 | `experiments/rollouts.rs` |
| `aos population` | Population runs (E2/E3, greedy against softmax) | `experiments/population.rs` |
| `aos compare` | Compare two measurement-A results | `experiments/compare.rs` |
| `aos show <file>` | Print a Parquet table as CSV | `io/parquet.rs` |

### 2.3 The pipeline of Claim 1

```
TSPLIB / generated instances
        │  aos candidates     (alpha lists, cached in data/cache)
        ▼
aos states    → tours at levels: random, 2-opt open, 2-opt optimum,
        │       3-opt optimum, LK optimum … (one file per instance)
        ▼
aos measure   → for each state and each operator: mean gain per
        │       application (untimed, every CPU) and mean time per
        │       application (timed, one pinned core) → rate = gain / time
        ▼
aos e1r       → from chosen states: try each operator as the first slice,
                then follow the greedy map; is greedy's first choice the
                best one over longer horizons?
```

`aos e0` sits beside this pipeline: it checks the clock is good enough
before anything timed is trusted.

### 2.4 The folders under `rust/`

| Path | What lives there |
|---|---|
| `Cargo.toml`, `Cargo.lock` | The package description and the exact library versions used |
| `build.rs` | A small program run before compiling, which records what the binary was built from |
| `src/lib.rs` | The library root: lists the modules |
| `src/main.rs` | The command-line program |
| `src/rng.rs` | Reproducible random-number streams |
| `src/instance/` | Reading instances, distances, neighbour and alpha candidate lists |
| `src/tour/` | Two ways of storing a tour, and reversal ("flip") of a path |
| `src/ops/` | The 16 operators |
| `src/timing/` | The cycle-counter clock, CPU pinning, machine checks, hardware counters |
| `src/io/` | Run directories, Parquet tables, provenance records |
| `src/experiments/` | E0, states, measurement A, E1R, population runs, compare |
| `tests/` | Integration tests (whole-library checks) |
| `tools/` | Shell scripts for locking the clock and for running whole nights |
| `DEVIATIONS.md` | Where the Rust operators differ from the old Python ones, and why |

---

## 3. Build files

### 3.1 `Cargo.toml`: the package description

Cargo is Rust's build tool and package manager, like `uv` plus
`pyproject.toml` together. This file says:

- **`[package]`:** the package is called `aos-rs` and uses the 2024
  edition of the language. `publish = false` keeps it off the public
  package index.
- **`[lib]` and `[[bin]]`:** it builds two things from one package:
  - a **library** called `aos` (`src/lib.rs`), which holds all the real
    code;
  - a **binary** (an executable program) also called `aos`
    (`src/main.rs`), which is a thin command-line layer over the
    library.

  The split lets the tests in `tests/` use the library directly.
- **`[dependencies]`:** the outside libraries ("crates"). What each one
  is for:

| Crate | Used for |
|---|---|
| `anyhow`, `thiserror` | Errors with readable messages (section 1.3) |
| `clap` | Parsing the command line into structs (`main.rs`) |
| `log`, `env_logger` | Timestamped log lines |
| `serde`, `serde_json` | Reading and writing JSON. `float_roundtrip` makes a float read back exactly as written (it once moved a cost coefficient by one last digit) |
| `arrow`, `parquet` | Writing result tables as Parquet files, which Python reads with pandas or polars |
| `rand_core`, `rand_xoshiro`, `rand` | The random-number generator and helpers |
| `sha2` | SHA-256 digests for provenance |
| `statrs` | Statistical distributions (t and normal quantiles for intervals) |
| `nalgebra` | Small linear algebra: the least-squares fit of the cost model |
| `libc`, `nix` | Calling operating-system functions (CPU pinning, process priority) |
| `perf-event2` | Reading hardware performance counters (cycles, cache misses) |
| `procfs` | Reading Linux's `/proc` files (other processes, interrupts) |
| `jiff` | Dates and times for timestamps |
| `rayon` | Easy parallel loops |

- **`[dev-dependencies]`:** `proptest`, used only by tests, generates
  many random inputs to check properties ("after any sequence of flips,
  the tour is still a valid cycle").
- **`[profile.release]`:** `opt-level = 3`, `lto = "fat"`,
  `codegen-units = 1`. All three ask for the most optimisation, at the
  cost of slower builds. Fat LTO lets the compiler optimise across the
  whole program at once. This matters because the experiments time
  individual operator applications.
- **`[lints.rust]`:** `missing_docs = "warn"` warns about any public item
  without a doc comment. `unsafe_op_in_unsafe_fn = "deny"` forces every
  unsafe operation to sit in its own explicit `unsafe` block, even inside
  an `unsafe fn`.

### 3.2 `rust-toolchain.toml` and `.cargo/config.toml`

- **`rust-toolchain.toml`** pins the compiler to version 1.99.1. Anyone
  building in this folder gets exactly that compiler, so the generated
  machine code, and with it the timing, does not change silently when a
  new Rust is released.
- **`.cargo/config.toml`** sets `target-cpu=x86-64-v3`. That tells the
  compiler it may use AVX2 instructions, but nothing newer. The binary
  is then the same on any reasonably modern x86 machine; with
  `target-cpu=native` it would depend on the exact CPU it was built on.

### 3.3 `build.rs`: recording what the binary was built from

**What.** A file named `build.rs` is a small program Cargo runs *before*
compiling the crate. This one computes facts about the build and bakes
them into the binary as constants:

- a SHA-256 digest of every source file;
- the digest of `Cargo.lock` (the exact library versions);
- the compiler version;
- the optimisation level and flags.

**How.**
- `files(directory, out)` walks a folder recursively and pushes every
  file path into the vector `out`. It takes `&mut Vec<PathBuf>`, a
  mutable borrow, so it can add to the caller's vector without taking
  it over.
- `hex(bytes)` turns raw bytes into a hex string (`format!("{byte:02x}")`
  per byte).
- `main()` collects `src/`, `build.rs` and `Cargo.toml` and sorts them
  so the order never changes. It then feeds each path, a zero byte, the
  file's contents and another zero byte into the hasher; the zero bytes
  keep "file A ends / file B starts" unambiguous.
- Each `println!("cargo:rustc-env=NAME=value")` is a message *to Cargo*.
  It sets an environment variable at compile time, which the code reads
  with `env!("AOS_SOURCE_SHA256")` (see `io/provenance.rs`).
- The `cargo:rerun-if-changed` lines tell Cargo when this script must run
  again.

**Why.** Every run directory records which code produced it. If you
change one line of an operator, the digest changes, and the analysis can
refuse to compare runs made by different code. This is how the E1R
analysis knew two runs were "the same implementation" before sharing a
calibration.

---

## 4. The entry points

### 4.1 `src/lib.rs`: the library root

It only lists the seven modules (`experiments`, `instance`, `io`, `ops`,
`rng`, `timing`, `tour`) and defines one type alias:

```rust
pub type City = u32;
```

A city is a number from `0` to `n − 1`, stored as a 32-bit unsigned
integer. `u32` is half the size of `usize`, so arrays of cities fit
twice as many entries in the CPU cache. An alias, rather than writing
`u32` everywhere, makes the meaning clear when you read a signature such
as `fn next(&self, city: City) -> City`.

### 4.2 `src/main.rs`: the command-line program

**What.** It turns `aos e1r --out runs/x --stage plan ...` into a call to
`rollouts::plan(...)`, and does the same for every other subcommand.

**How: the command line is described as types.** This is the `clap`
library's "derive" style:

- `struct Cli` is the whole command line. It has one global option,
  `--repository`, which defaults to the folder above `rust/` (computed at
  compile time from `CARGO_MANIFEST_DIR`), and one field `command`.
- `enum Command` has one variant per subcommand (`Instance`,
  `Candidates`, `Clock`, `E0`, `E0Gates`, `States`, `Measure`, `E1r`,
  `Population`, `Compare`, `Show`). Each variant's fields are that
  subcommand's options. The `///` comments above each field are not just
  documentation: `clap` prints them as the `--help` text.
- `#[arg(long)]` makes a field a `--name` option. `default_value_t = 64`
  gives a default. A field of type `Option<T>` is an optional flag (it is
  `None` if the flag is absent). `Vec<T>` with `num_args = 1..` takes one
  or more values.
- The subcommands with many options get their own structs:
  `MeasureArgs`, `E1rArgs`, `PopulationArgs`. `#[derive(clap::Args)]`
  lets them be embedded.
- `enum E1rStage` (`Plan`, `Calibrate`, `Run`, `Validate`, `Analyse`)
  and `enum Preset` (`Full`, `Budgeted`, `Quick`) are option values;
  `#[derive(clap::ValueEnum)]` lets them be typed on the command line as
  `--stage plan`.

**The functions.**
- **`main()`**:
  1. calls `timing::disturb::keep_heap()`, so later memory allocation
     does not hand memory back to the operating system in the middle of
     a timed loop (section 9);
  2. starts the logger at level `info`;
  3. parses the command line with `Cli::parse()`;
  4. turns the repository path into an absolute path (`canonicalize`);
  5. `match`es on the subcommand and calls the right function.

  Because `main` returns `anyhow::Result<()>`, any error that reaches it
  is printed with its whole context chain and the program exits with a
  non-zero code.
- **`resolve(repository, path)`**: makes a path absolute. A relative
  path is taken relative to the repository root, not to the folder you
  happen to run from. That is why the e1r script can pass
  `runs/e1r-night/...`.
- **`e1r(repository, args)`**: one `match` arm per stage.
  - `Plan` builds a `rollouts::Settings` from the options.
    `--measurement` is required here, hence `.ok_or_else(...)?`, which
    turns a `None` into an error. The fixed values `fit_seeds: [0, 1,
    2]`, `check_seed: 3` and `seed: 0` are protocol constants, not
    options.
  - `Calibrate` either copies the cost models of another run
    (`--cost-models-from`, the shared calibration of the 2026-11-05
    night) or times new ones.
  - The other three call `run`, `validate` and `analyse`.
- **`measure(repository, args)`**:
  1. starts from a preset (`marginal::Settings::full`, `budgeted` or
     `quick`) and overwrites each field you gave on the command line
     (`if let Some(value) = args.repeats { settings.repeats = value }`);
  2. checks `2 ≤ min_rounds ≤ max_rounds`;
  3. fills in the implementation choices (distance storage, cache, tour
     layout, the size `n_split` from which the two-level tour is used);
  4. calls `marginal::run`.
- **`population_runs(repository, args)`**: the same pattern for
  `population`. Each `--arms` string (such as `fixed:lk_kick` or
  `softmax:0.5`) is parsed by `population::ArmSpec::parse`; `.collect::<
  anyhow::Result<_>>()?` turns "a list of results" into "a result holding
  a list", failing on the first bad arm.
- **`instance_info(repository, paths)`**: loads each instance and prints
  its name, size, known optimum, kind of coordinates, and whether its
  alpha candidates are cached.
- **`candidates(repository, paths, workers)`**: computes alpha candidate
  lists for many instances in parallel, written by hand rather than with
  rayon.
  - **Shared state:** an `AtomicUsize` counter `next` is the shared "next
    instance to do", and a `Mutex<Vec<String>>` collects error messages.
  - **Workers:** inside `std::thread::scope`, each worker loops. It claims
    a number with `next.fetch_add(1, ...)`, takes that path, or stops when
    there are none left (`let Some(path) = paths.get(index) else { break
    };`). It then loads the instance and calls `ensure_alpha`, which
    computes and caches the candidates unless they exist already.
  - **Failures:** a failure does not stop the others; its message goes
    into the error list. After all workers finish, one error lists every
    failed instance.

  Why by hand: some instances take minutes and others milliseconds; a
  shared counter gives each worker a new instance the moment it is free.
- **`clock()`**:
  1. calibrates the CPU's timestamp counter (5 rounds of 200 ms);
  2. prints the result as JSON;
  3. lists the physical cores;
  4. reads the machine state (boost, governor, SMT and so on);
  5. prints either "machine locked for timing" or the list of problems.

  It is a quick check before a timed run.

---

## 5. `src/rng.rs`: seeded random streams

### 5.1 Why a file just for random numbers

Every random operator (a random 2-opt move, a double-bridge kick) uses
random numbers. The experiments need two things:

1. **Exact replay.** Re-running the same application (same state,
   operator, anchor and repeat) must give the same random draws, so a
   result can be checked or timed again.
2. **Independence from order and threads.** Gains are computed on every
   CPU in parallel, in whatever order the threads happen to take jobs.
   If all applications shared one generator, the draws would depend on
   that order.

The answer is **one fresh generator per application**, created from a
key that names the application. This is the "common random numbers" idea
used in E1R: the same key gives the same stream whichever operator is
being tried.

### 5.2 The pieces

- **`SCHEME`**: a constant string naming the method, written into each
  run's provenance, so a later reader knows how the random numbers were
  made.
- **`splitmix64(state)`**: one step of SplitMix64, a tiny and well-known
  generator. It adds a constant to `state` and scrambles the result with
  shifts and multiplications. `wrapping_add` and `wrapping_mul` mean
  "let the number overflow and wrap around": in Rust, ordinary `+`
  panics on overflow in debug builds, so overflow that is intended must
  be written explicitly. It is used only to **mix keys into a seed**, not
  to make the operators' draws.
- **`label_hash(label)`**: turns a text label such as `"kroA100:s0:opt2"`
  into a 64-bit number, from the first 8 bytes of its SHA-256 digest.
  SHA-256 is overkill for randomness, but it guarantees two different
  labels give unrelated numbers.
- **`struct Stream(Xoshiro256PlusPlus)`**: a "tuple struct" with one
  unnamed field, the actual generator. xoshiro256++ is a fast,
  high-quality, standard generator. Wrapping it in our own type means the
  rest of the code only sees the few draws listed below.

### 5.3 `Stream` methods

- **`Stream::new(seed, label, keys)`** builds a stream from a run seed, a
  label and any number of integer keys (for example `[operator, anchor,
  repeat]`).
  1. Start from `seed`, run one SplitMix64 step, and mix in the label's
     hash with XOR (`^=`).
  2. For each key, run another step and mix the key in. The steps between
     keys make the order matter: keys `[3, 7]` and `[7, 3]` give
     different streams, which a test checks.
  3. Take four more SplitMix64 outputs as the generator's 256-bit seed.

  `bytes.as_chunks_mut::<8>()` splits the 32-byte array into four 8-byte
  pieces to fill one at a time.
- **`next_u64()`**: the next 64 random bits.
- **`below(bound)`**: a whole number from `0` to `bound − 1`, every value
  equally likely.
  - **The obvious way is biased.** `next_u64() % bound` favours small
    numbers slightly, because 2⁶⁴ is not a multiple of `bound`.
  - **Lemire's method avoids the bias.** Multiply the random number by
    `bound` in 128-bit arithmetic and keep the top 64 bits; that is the
    answer. In the rare case where the low half shows the result would be
    biased, draw again. The `assert!` crashes if `bound` is 0, because
    there is no valid answer.
- **`index(bound)`**: the same for array indices (`usize`).
- **`unit()`**: a float in `[0, 1)`. It keeps 53 random bits (the
  precision of an `f64`) and scales them.
- **`sample_from(distribution)`**: one draw from any distribution of the
  `rand` library (for example a normal distribution), using this
  stream's generator.
- **`shuffle(items)`**: the Fisher–Yates shuffle. Walk from the end of
  the slice to the start, and swap each position with a random position
  at or before it. This is the shuffle behind `nested_draw` in E1R.
- **`sample(population, count)`**: `count` distinct numbers from
  `0..population`. It keeps drawing and skips repeats. That is fine for
  the small counts used here, but would be slow if `count` were close to
  a large `population`.

### 5.4 Tests

- `streams_depend_on_every_key`: changing the seed, the label, any key,
  or the order of keys changes the stream; the same inputs give the same
  stream.
- `below_stays_in_range_and_covers_it`: 1,000 draws below 7 hit every
  value.
- `sample_is_distinct`: sampling 10 of 10 gives each number once.

---

## 6. `src/instance/`: problems, distances, candidate lists

An *instance* is one TSP problem: `n` cities and the distance between
every pair. This folder reads instances from TSPLIB files, stores the
distances, and builds the two kinds of candidate lists the operators
search: the 10 nearest neighbours, and the 5 alpha-nearest candidates
LKH uses.

### 6.1 `instance/tsplib.rs`: reading TSPLIB files

**What.** It parses the text format of TSPLIB (`NAME : kroA100`,
`DIMENSION : 100`, then a `NODE_COORD_SECTION` with one line per city).
It supports only the variants the experiments use:

- coordinates with `EUC_2D` (rounded Euclidean), `ATT` (a
  pseudo-Euclidean variant used by att48 and att532) and `GEO`
  (distances on the globe);
- an explicit matrix in four layouts.

**Why so strict.** Anything else is rejected with an error that names
the unsupported thing. A silently mis-read instance would give wrong
optima and wrong gaps without any sign of a problem.

**The types.**
- **`enum TsplibError`**: the two ways reading can fail.
  - `Io`: the file cannot be read.
  - `Format`: the file is malformed or unsupported.

  `#[derive(Error)]` from `thiserror` and the `#[error("{path}:
  {message}")]` lines define the printed message.
- **`enum CoordinateKind`**: `Euc2d`, `Att`, `Geo`.
- **`enum Weights`**: how distances are given. Either
  `Coordinates { kind, points }` (one `[x, y]` pair per city) or
  `Explicit(Vec<i32>)`, a full `n × n` matrix stored row after row in one
  long vector (the entry for row `i`, column `j` is at position
  `i * n + j`).
- **`struct TsplibFile`**: the parsed result: `name`, `dimension` (the
  number of cities), `weights`.
- **`SECTIONS`**: the list of keywords that start a data section.

**The functions.**
- **`split(text, path)`** divides the file into its two parts.
  - The **header** is the `KEY : value` lines; keys are upper-cased so
    `name` and `NAME` both work.
  - The **sections** are each keyword such as `NODE_COORD_SECTION` with
    the lines that follow it, up to the next keyword.

  It walks the lines with an index instead of a `for` loop, because it
  needs to look ahead to find where a section ends. Any line it does not
  understand is an error, not something to skip.
- **`numbers(rows, path, section)`** splits the section's lines into
  tokens and parses each as a float. `.collect()` into a
  `Result<Vec<f64>, _>` stops at the first bad token and returns its
  error; this is a common Rust pattern.
- **`integral(value, path)`** converts a weight to `i32` and refuses
  fractions, negatives and numbers too big for 32 bits.
- **`unpack(values, n, layout, path)`** turns a packed explicit matrix
  into a full one.
  - **The layouts.** `UPPER_ROW` lists only the entries above the
    diagonal, row by row; `LOWER_DIAG_ROW` lists the lower triangle with
    the diagonal; and so on.
  - **How the layouts are handled.** The function first writes down, for
    the chosen layout, the sequence of `(row, column)` positions in the
    order the file lists them (the `Box<dyn Iterator ...>` lines). It
    then pairs that sequence with the values. Each value is written to
    `(i, j)` and mirrored to `(j, i)`.
  - **Checks.** The value count must match the layout. A full matrix must
    be symmetric.
- **`read(path)`** is the public entry point.
  1. Reads the file and calls `split`.
  2. Defines two small closures, `field("NAME")` and
     `section("NODE_COORD_SECTION")`, to look things up.
  3. Checks `TYPE` is `TSP` and `DIMENSION` is at least 8; the double
     bridge needs room for four cut points.
  4. Builds the `Weights` according to `EDGE_WEIGHT_TYPE`.

  `GEO` coordinates are converted to radians straight away.
  `values.as_chunks::<3>()` groups the coordinate section into `[index,
  x, y]` triples.
- **`read_tour(path, dimension)`** reads a `.tour` file (TSPLIB numbers
  cities from 1; the code stores them from 0). It checks every city
  appears exactly once. `std::mem::replace(&mut seen[c], true)` sets the
  flag and returns its old value in one step, so a `true` means "seen
  before".

### 6.2 `instance/distance.rs`: the TSPLIB distance formulas

Each function reproduces TSPLIB's reference code exactly, so that the
published optimal tour lengths stay valid.

- **`nint(value)`**: TSPLIB's rounding, `floor(x + 0.5)`, which rounds
  halves up.
- **`euc_2d(a, b)`**: straight-line distance, rounded.
- **`att(a, b)`**: `sqrt((dx² + dy²) / 10)`, rounded, then increased by
  one if rounding went down.
- **`geo_radians(value)`**: TSPLIB writes geographic coordinates as
  `DDD.MM`, degrees and minutes. This converts to radians.
- **`geo(a, b)`**: the great-circle distance in kilometres on a sphere of
  radius 6,378.388 km, as TSPLIB defines it.
- **`TSPLIB_PI = 3.141592`**: TSPLIB's code uses this truncated value of
  pi, not the exact one. With the exact value, a few distances in
  gr96, gr202, gr431, gr666 and ali535 change by one, and the listed
  optima would no longer match. The `#[allow(clippy::approx_constant)]`
  line tells the linter that the inexact pi is deliberate.

Tests check rounding cases and that the GEO distance of a point to
itself is 1 (a quirk of TSPLIB's `+ 1.0` in the formula).

### 6.3 `instance/storage.rs`: where distances live

**What.** It defines how operators read a distance, and two ways to
store them.

- **`trait Dist`** is the interface: `n()` (number of cities) and
  `d(i, j)` (distance between two cities). Every operator is written
  against this trait, not against a particular storage. `: Sync` means
  the storage may be read from several threads at once.
- **`struct Matrix`** is the main design: all `n²` distances in one
  `Vec<i32>`, row by row. `d(i, j)` is one array read at `i * n + j`.
  - `Matrix::new(n, values)` wraps an existing vector and checks its
    length.
  - `Matrix::from_computed(computed)` fills the matrix from coordinates,
    and fails if any distance does not fit in `i32`. (This check is what
    LKH lacked in the netgen incident of the headroom test.)
  - `row(i)` returns the distances from city `i` as a slice.
- **`struct Computed`** is the alternative: keep only the coordinates and
  compute every distance when it is asked for. `exact(i, j)` calls the
  right formula from `distance.rs`. It exists for a robustness factor of
  measurement A: are the operator rankings the same when a distance read
  costs a square root instead of a memory read?

**Why a trait and generics here.** The operators are generic
(`fn two_opt<D: Dist>(dist: &D, ...)`), so the compiler builds one copy
of every operator for `Matrix` and another for `Computed`. Inside each
copy, `d(i, j)` is a direct, inlined read (`#[inline(always)]`), with no
run-time decision about which storage to use. That matters because
distance reads are the innermost loop of every operator, and timing is
the subject of the study.

**Why `i32`.** A distance needs 4 bytes instead of 8, so twice as many
fit in the CPU cache. Sums of distances (tour lengths, gains) are
computed in `i64` so they cannot overflow.

### 6.4 `instance/neighbours.rs`: candidate lists

**What.** For each city, a short list of other cities to try connecting
it to. Operators search these lists instead of all `n − 1` cities. That
is the "neighbour list" speed-up of Johnson and McGeoch, and the reason
2-opt with neighbour lists is much faster than full 2-opt.

- **`NEIGHBOURS = 10`**: the 10 nearest neighbours per city.
- **`ALPHA_CANDIDATES = 5`**: the 5 alpha-nearest candidates per city,
  LKH's default.
- **`struct CandidateLists`** stores `k` candidates per city in one flat
  vector (`cities`), plus their distances in a second vector
  (`distances`).
  - **Why the distances are stored too.** The operators' inner loop reads
    "candidate `c` of city `a` and the distance `d(a, c)`". Reading the
    distance from this small, contiguous array is much more
    cache-friendly than jumping into row `a` of an `n × n` matrix.
  - **The design note** calls this "option C" (the `cache` setting of
    measurement A). The work counters count matrix reads and cache reads
    separately.
- **`from_lists(lists, dist)`** builds the structure from one `Vec` of
  candidates per city. It checks:
  - every list has the same length;
  - no list names the city itself or a city out of range;
  - each list is sorted by distance.

  The sorting matters: the operators stop scanning a list as soon as the
  candidate is too far to give any gain (the "neighbour-list stopping
  rule"), which only works on a sorted list.
- **`nearest(dist, k)`** builds the k-nearest lists. For each city:
  1. list `(distance, other city)` for all others;
  2. use `select_nth_unstable(k − 1)` to move the `k` smallest to the
     front without sorting everything (much faster than a full sort when
     `n` is large);
  3. cut the list at `k` and sort those `k`.

  Sorting pairs `(distance, city)` breaks ties by the smaller city
  number, the same rule the old Python code used.
- **`k()`, `cities(city)`, `distances(city)`**: read access; the last two
  return the slice for one city.
- **`to_lists()`**: back to a `Vec` per city, for files and comparisons.

### 6.5 `instance/alpha.rs`: LKH's alpha-nearness candidates

This is the most mathematical file of the folder. Here it is step by
step.

**The idea (Helsgaun 2000).** Nearest neighbours are a crude guess at
which edges an optimal tour uses. LKH uses a better guess, based on
**1-trees**.

- **What a 1-tree is.** Pick one special city. Build a minimum spanning
  tree over all the others, then connect the special city with its two
  cheapest edges. Every tour is a 1-tree (where every city has degree 2),
  so the cheapest 1-tree is a lower bound on the tour length.
- **The alpha value of an edge (i, j)** is how much more expensive the
  cheapest 1-tree becomes if it is *forced* to contain that edge. Edges
  with alpha 0 are already in the cheapest 1-tree. A small alpha means
  "nearly free to include", which turns out to predict optimal-tour
  edges much better than plain distance.
- **Penalties make the bound tighter.** Before computing alpha, LKH adds
  a penalty `π` to every city, so that the transformed distances are
  `d'(i, j) = d(i, j) + π_i + π_j`. The penalties are adjusted until the
  1-tree has as many degree-2 cities as possible ("subgradient ascent").
  This tightens the bound and improves the candidates. In the headroom
  test, this ascent is the "preprocessing" that took 84 s at 10,000
  cities.

**The code.**
- **`ALPHA_CODE_VERSION`**: a number bumped whenever the computation
  changes. Every cached result with an older version is recomputed, so a
  bug fix can never be hidden by an old cache file.
- **`struct AscentSettings`**: `max_iterations` (300) and `initial_step`
  (1.0). LKH has no iteration cap; this one keeps very large dense
  instances affordable. `impl Default` gives these values when you write
  `AscentSettings::default()`.
- **`struct OneTree`** is the result of building a 1-tree:
  - each city's parent in the tree;
  - the order in which cities were added;
  - the total weight;
  - each city's degree.
- **`weight(dist, pi, i, j)`**: the transformed distance `d(i, j) + π_i +
  π_j`. The additions happen in the same order as the old Python code,
  because floating-point addition is not exactly associative and the
  candidates were compared against Python's.
- **`minimum_one_tree(dist, pi, special)`**: Prim's algorithm. It starts
  from a root city and repeatedly adds the closest city not yet in the
  tree, keeping for each outside city its best connection so far
  (`best`, `best_from`). This is the simple O(n²) version, which never
  stores a distance matrix of transformed weights: it computes them on
  the fly, so memory stays O(n) even for rl11849. At the end it adds the
  special city's two cheapest edges, from `two_smallest`.
- **`two_smallest(dist, pi, city)`**: one pass over all other cities,
  keeping the cheapest and second-cheapest.
- **`ascend(dist, settings)`**: subgradient ascent, following LKH's
  `Ascent.c`. Each iteration does this:
  1. Build the cheapest 1-tree with the current penalties. The bound is
     its weight minus `2 × sum(π)`.
  2. Compute `v = degree − 2` for every city. A city with degree 3 has
     `v = 1` (too many tree edges); a leaf has `v = −1`.
  3. Record the best bound and its penalties.
  4. Move the penalties in the direction of `v` (cities with too many
     edges get more expensive). The step uses `0.7 × v + 0.3 ×
     previous v`, a small momentum term, also from LKH.

  The step size and the "period" (iterations before halving the step)
  follow LKH's schedule: the step doubles while the bound keeps
  improving at the start, then halves at the end of each period. The
  loop stops when every degree is 2 (the 1-tree *is* a tour, so the
  bound is optimal), the iteration cap is reached, or the step becomes
  tiny. It returns the penalties of the *best* bound seen, not the
  last.
- **`best_k(alpha, distances, k)`**: picks the `k` cities with the
  smallest alpha (ties broken by distance, then by city number), then
  re-sorts those `k` **by distance**. That re-sort is a deliberate
  difference from LKH, which orders candidates by alpha. Here the lists
  must be sorted by distance so the operators' stopping rule works.
  `total_cmp` compares floats in a way that is defined even for NaN, and
  `.then(...)` chains tie-breakers.
- **`alpha_candidates(dist, pi, k)`**: computes alpha for every pair
  without storing an `n × n` table, in O(n²) time and O(n) memory.
  - **What β is.** For a fixed city `i`, `β[j]` is the largest edge
    weight on the tree path from `i` to `j`. Adding edge (i, j) to the
    tree and removing that heaviest edge gives the cheapest 1-tree that
    contains (i, j), so `alpha(i, j) = d'(i, j) − β[j]`.
  - **How β is computed.** First walk up from `i` to the root, filling β
    along that path and marking those cities. Then visit the other cities
    in Prim's order, so every city's parent is done before it, and take
    `β[j] = max(β[parent], weight of j's tree edge)`.
  - **The special city 0** is handled separately with the second-cheapest
    edge rule.
- **Caching.**
  - `CacheKey` holds the instance file's SHA-256, `k`, the ascent
    settings and the code version.
  - `CacheFile` is the key plus the lists, written as JSON under
    `data/cache/candidates-rs/<name>-alpha5.json` (`cache_path`).
  - `load_cached` returns the lists only if the stored key equals the
    current key. Otherwise it logs a warning and returns `None`, so the
    lists are recomputed.
  - `store` writes to a temporary file and then renames it. A rename is
    atomic, so a crash halfway never leaves a half-written cache file
    that looks valid.

### 6.6 `instance/mod.rs`: the `Instance` struct

**`struct Instance`** gathers everything about one problem:

| Field | Meaning |
|---|---|
| `name`, `path` | TSPLIB name and file path |
| `sha256` | Digest of the file, so a run can prove which file it used |
| `matrix` | The dense distance matrix (always present) |
| `computed` | Coordinates, if the instance has them (`Option`) |
| `neighbours` | The 10-nearest lists |
| `alpha` | The 5 alpha-nearest lists, if computed or cached (`Option`) |
| `optimum` | The optimal or best-known length, if known (`Option`) |

**The functions.**
- **`sha256_hex(bytes)`**: digest as hex text.
- **`tour_length(dist, order)`**: sums the distances around a closed
  tour, starting from the edge between the last and the first city.
  `impl Dist` in the argument means "any type that implements `Dist`".
- **`listed_optimum(listing, name)`**: looks for `name : length` in a
  `solution.txt` file next to the instance. It returns `Ok(None)` if the
  file or the entry is missing, and an error if the entry exists but is
  not a whole number. Missing data is normal; a malformed entry is a
  mistake that should be loud.
- **`companion_tour(path)`**: looks for `<stem>.opt.tour` or `<stem>.tour`
  next to the instance. `.find(...)` returns the first that exists.
- **`Instance::load(path, alpha_cache)`**:
  1. Read the bytes and hash them.
  2. Parse the file (`tsplib::read`).
  3. Build the matrix: either take the explicit one, or compute it from
     the coordinates and keep the coordinates as `computed`.
  4. Build the 10-nearest lists.
  5. Find the optimum. If there is a companion tour, its length *is* the
     optimum; this is more trustworthy than a number in a text file.
     Otherwise use `solution.txt`.
  6. Attach cached alpha candidates if a matching cache exists.

  It does **not** compute alpha candidates, because that can take
  minutes.
- **`n()`**: number of cities.
- **`alpha_key(ascent)`**: the cache key for this instance.
- **`attach_alpha(lists)`** (private): validates lists through
  `CandidateLists::from_lists` and checks there are 5 per city (fewer
  only if the instance has fewer than 6 cities).
- **`ensure_alpha(alpha_cache)`**: if alpha lists are not attached yet,
  run the ascent, compute the candidates, store them in the cache and
  attach them. It returns `true` if it computed them, `false` if they
  were already there. `aos candidates` calls it.

---

## 7. `src/tour/`: how a tour is stored and changed

### 7.1 The problem this folder solves

A tour is a cycle through all cities. The operators ask it questions
("which city comes after 17?") and change it ("reverse the path from 17
to 42", the basic move of 2-opt). The two jobs pull in different
directions:

- An **array** of cities in visiting order answers questions instantly,
  but reversing a path of length `k` costs `k` writes, up to `n/2`.
- A **two-level list** (cities in blocks of about √n, each block with a
  "reversed" flag) reverses a path by reordering and flagging whole
  blocks, which costs about √n. Each question costs a little more.

At 100 cities the array wins; at 10,000 cities the two-level list wins.
E0-rs measured the crossover, and the experiments use the array below
`n_split = 1000` and the two-level list from there on, as LKH does.
Both layouts are here, behind a common interface, so the operators are
written once.

A third concern is **undo**. Measurement A applies an operator, records
the gain and the time, and must then put the tour back exactly, many
thousands of times per state. Copying an n-city tour each time would
cost O(n) and would dominate the timing. So every change is logged, and
undo replays the log backwards.

### 7.2 `tour/mod.rs`: the shared interface and the `Tour` wrapper

**`trait Layout`** is the interface both storages implement. It is
written in **logical** terms only:

| Method | Meaning |
|---|---|
| `NAME` | `"array"` or `"two_level"`, for provenance |
| `from_order(order)` | Build from a visiting order |
| `n()` | Number of cities |
| `next(c)`, `prev(c)` | The city after / before `c` |
| `steps(a, c)` | How many forward steps from `a` to `c` |
| `walk(c, k)` | The city `k` steps forward from `c` |
| `reverse(a, b)` | Reverse the forward path from `a` to `b`; returns how many storage positions were rewritten |
| `swap(a, b)` | Exchange the places of two cities |
| `check_consistent()` | Verify internal bookkeeping (used by tests) |

`Clone + Send + Sync` in the trait's first line means every layout can
be copied and used from several threads.

**"Logical orientation" (the comment at the top of the file).** When
you reverse the path `a..b`, a layout may physically rewrite either the
path or the rest of the tour (the "complement"), whichever is shorter.
Both give the same cycle, but in opposite directions of travel. To
stop the direction from depending on that choice, each layout keeps a
**global reversal bit**. After rewriting the complement, it flips the
bit, so `next` and `prev` still describe the same direction as if the
path itself had been reversed. Result: the *logical* tour after any
sequence of moves is identical in both layouts, and an undo restores it
exactly. Deviation D2 in `DEVIATIONS.md` records this as a difference
from the old Python code, where the direction flipped with whichever
side was rewritten.

**`enum Change`** is one entry of the undo log:
- `Reverse(a, b)`: undone by reversing `b..a`;
- `Swap(a, b)`: undone by swapping again;
- `Replace(old order)`: the whole order was replaced (ruin & recreate
  does this); undone by rebuilding from the stored old order.

**`struct Mark`** is a bookmark: the log length and the tour length at
some moment. **`struct Tour<L: Layout>`** is the type the rest of the
code uses: a layout, the current length (kept up to date by the
operators, never recomputed), and the log. `<L: Layout>` means `Tour` is
generic over the layout: `Tour<Array>` and `Tour<Blocks>` are two
different, separately optimised types.

**Methods of `Tour`.**
- **`new(order, dist)`**: checks the order is a permutation (crashes
  otherwise; callers validate first), builds the layout and computes the
  length once.
- **`n`, `length`, `next`, `prev`, `steps`, `walk`**: pass through to the
  layout. `walk` takes `k` modulo `n`.
- **`add_length(delta)`**: operators compute the gain of a move from
  the few edges it changes and add it here. That is why tour length
  never needs a full recomputation.
- **`succ_in(c, forward)`**: `next` or `prev` depending on a flag. Many
  operators try both directions; this avoids writing every loop twice.
- **`between(a, b, c)`**: is `b` on the forward path from `a` to `c`?
  This is the classic `between` test of 2-opt and Or-opt implementations.
- **`reverse(a, b)`, `swap(a, b)`**: log the change, then make it.
  Reversing a path of one city does nothing and logs nothing.
- **`replace(order)`**: logs the whole old order, then rebuilds the
  layout from the new one. It costs O(n), counted as `n` positions
  moved. The `debug_assert!` checks the order only in debug builds,
  because the check itself costs O(n).
- **`mark()`**: returns a bookmark of the current state.
- **`undo_to(mark)`**: pops log entries until the log is back to the
  mark's length, undoing each, then restores the stored length. It
  returns how many positions were rewritten, which the work counters use.
- **`replay_since(mark)` and `replay(replay)`**: these exist to *time*
  how much an undo costs, without the operator's search.
  - `replay_since` records the changes made since a mark.
  - `replay` re-applies them to a tour in the earlier state.

  A timed loop of "replay, then undo" therefore costs two restores per
  application. Measurement A uses this to subtract the restore cost from
  the operator's measured time.
- **`clear_log()`**: forget the log, making the changes permanent.
  Without this, the log of a long search would keep growing.
- **`touched_since(mark, out)`**: after a move, lists the cities at its
  ends and their current neighbours. Local searches with "don't-look
  bits" (2-opt descent, Or-opt, LK) put these cities back on their work
  queue, because only near a changed edge can a new improvement appear.
- **`normalize()`**: rebuilds the storage from the logical tour. After
  an undo, the two-level layout may be split into blocks differently
  from before, even though the logical tour is identical. The next
  application would then rewrite a different number of positions and
  take a different time. Timing code calls `normalize` before every
  timed sample so every sample starts from the same storage.
- **`order()`**: the visiting order starting from city 0.
- **`hash()`**: a 64-bit fingerprint (FNV-1a) of the cycle. It starts at
  city 0 and goes towards city 0's smaller-numbered neighbour, so the
  same cycle gives the same hash however it is rotated or which
  direction it is stored in. It is used to recognise identical tours,
  for example duplicate states.
- **`check(dist)`**: a full self-check: layout bookkeeping, permutation,
  `prev(next(c)) == c` for all cities, and the tracked length equals a
  fresh recomputation. Tests call it after random sequences of moves.
- **`same_as(other)`**: two tours are logically equal if every city has
  the same `next`.

**`validate_order(order, n)`**: checks an order has `n` entries, each
city in range and none repeated.

### 7.3 `tour/array.rs`: the array layout

**The data.**
- `order[k]` is the city at storage position `k`.
- `pos[c]` is the storage position of city `c`. It is the inverse of
  `order`, so both "who is at position k" and "where is city c" take one
  read.
- `reversed` is the global bit: if set, read `order` backwards.

**The methods.**
- **`logical(c)`**: city `c`'s position counted in the logical
  direction.
- **`next(c)` and `prev(c)`**: look one step forward or back in `order`
  (backwards if `reversed`), wrapping around at the ends.
- **`steps(a, c)`**: the difference of logical positions, modulo `n`.
- **`walk(c, k)`**: logical position plus `k`, converted back to a
  storage index.
- **`reverse(a, b)`**:
  1. Work out the storage endpoints. If `reversed` is set, the logical
     path `a..b` is stored as `b..a`.
  2. If the path spans at most half the tour, swap pairs from both ends
     inwards (`reverse_storage`).
  3. Otherwise reverse the complement (everything outside the path)
     instead and flip the global bit.

  Each swap updates both `order` and `pos`. It returns the number of
  positions written. The `assert!` refuses to reverse the whole tour,
  which would be a no-op that some operator logic would then misread.
- **`swap(a, b)`**: exchange two cities: two writes in `order`, two in
  `pos`.
- **`check_consistent()`**: `pos` and `order` must be exact inverses.

### 7.4 `tour/blocks.rs`: the two-level layout

**The idea.** Cut the tour into about √n blocks of about √n cities.
Each block has its own `flipped` bit, meaning "read my cities
backwards". To reverse a long path:

1. split blocks so that the path starts and ends exactly at block
   boundaries (at most two splits);
2. reverse the *order of the blocks* in the path;
3. toggle each of those blocks' `flipped` bits.

That touches about √n blocks instead of up to n/2 cities. Splits create
extra small blocks; when there are more than twice the original number
(`limit`), the whole layout is rebuilt into even blocks (O(n), but rare,
so the average cost stays about √n). This is the structure of Fredman et
al. (1995) and of LKH. Here the blocks are kept in a vector with ranks
and offsets instead of LKH's linked list, which costs the same
per reversal (deviation D12). A test in `tests/external.rs` checks it
against Concorde's C implementation flip by flip.

**The data.**

| Field | Meaning |
|---|---|
| `n` | Number of cities |
| `target` | Block size of a fresh layout, ⌈√n⌉ |
| `limit` | Block count above which to rebuild |
| `cities[b]` | The cities of block `b`, as stored |
| `flipped[b]` | Whether block `b` is read backwards |
| `order` | Block ids in storage order |
| `rank[b]` | Where block `b` sits in `order` |
| `offset[b]` | Storage position of block `b`'s first city |
| `block_of[c]`, `index[c]` | Which block city `c` is in, and where inside it |
| `reversed` | The global reversal bit, as in the array |

**The methods**, from the inside out:
- **`from_storage(sequence, reversed)`**: cut a sequence into blocks of
  `target` cities, then fill the lookup tables (`reindex` for each block,
  then `renumber`).
- **`reindex(block)`**: for each city in a block, record its block and
  index.
- **`renumber()`**: walk `order` and recompute each block's rank and
  offset. It is O(number of blocks) = O(√n).
- **`inner(c)`**: city `c`'s index within its block, in reading
  direction (flipped blocks are read from the end).
- **`storage(c)`**: the block's offset plus `inner(c)`, i.e. `c`'s
  storage position.
- **`logical(c)`**: the storage position, or its mirror if `reversed`.
- **`at(position)`**: the city at a storage position. It finds the block
  by binary search on offsets (`partition_point`), then the city inside
  it. `walk` uses it, so `walk` costs O(log √n) here instead of O(1).
- **`first_of(block)`, `last_of(block)`**: the first and last city in
  reading direction.
- **`block_after(block)`, `block_before(block)`**: neighbours in block
  order, wrapping around.
- **`storage_next(c)`, `storage_prev(c)`**: step within the block if
  possible, otherwise jump to the first (or last) city of the
  neighbouring block. `next` and `prev` call these, swapped if the global
  bit is set.
- **`split_before(c)`**: make `c` the first city of a block. The part
  of `c`'s block from `c` on moves into a new block inserted right
  after. For a flipped block, "from `c` on in reading direction" is the
  *front* of the stored vector, hence the two branches. It returns how
  many cities were re-indexed.
- **`reverse_storage(first, last)`**: the three steps above:
  1. split before `first` and before the city after `last`;
  2. reverse the run of blocks between them in `order` and toggle their
     bits;
  3. renumber.

  If the block count passed `limit`, rebuild everything. It returns
  positions touched, so the work counter reflects both splits and
  rebuilds.
- **`storage_sequence()`**: all cities in storage order, used by the
  rebuild.
- **`Layout` methods**:
  - `reverse` picks the path or its complement, whichever is shorter,
    exactly as the array does.
  - `swap` exchanges two cities in their blocks' vectors and updates the
    lookups.
  - `check_consistent` verifies that no block is empty, every rank and
    offset is fresh, every `block_of` and `index` is right, and the
    blocks hold exactly `n` cities.

---

## 8. `src/ops/`: the operators

### 8.1 What every operator looks like

All 16 operators share one shape. An *application* takes:

- the tour (`&mut Tour<L>`, changed in place);
- the problem (`&Problem`: distances and candidate lists);
- a random stream (`&mut Stream`);
- the work counters (`&mut Work`);
- an optional *anchor*, the city the move is centred on. If none is
  given, a random city is drawn (`pick_anchor`).

It returns an `Application`: `delta`, the change in tour length (negative
if the tour got shorter, otherwise exactly 0), and the anchor actually
used.

**The contract.** An operator either improves the tour or leaves it
logically unchanged. There are two kinds:

- **Moves** (2-opt, Or-opt, 3-opt, LK) look for an improving change and
  apply it only if they find one.
- **Trials** (kicks, ruin & recreate) make a change that usually makes
  the tour worse, repair it with a local search, and keep the result only
  if the final tour is shorter; otherwise they undo everything through
  the undo log.

That is why every operator's "gain" in measurement A is never negative.

### 8.2 `ops/mod.rs`: the shared types and the `Op` enum

- **`struct Work`** counts work done:
  - `matrix_reads`: distances read from the matrix (or computed);
  - `cache_reads`: distances read from a candidate list's stored
    distances;
  - `positions`: storage positions rewritten by tour changes;
  - `flips`: path reversals, including undone ones.

  `lookups()` and `moved()` give the totals under the old Python names.
  These counts feed E1R's cost model: time ≈ a + b·lookups + c·moved.
- **`struct Problem<'a, D, const CACHE: bool>`** is what an operator sees
  of the instance:
  - `dist`: the distance storage;
  - `neighbours`: the 10-nearest lists;
  - `alpha`: the alpha lists, if any.

  Two new things in the type:
  - **`'a`** is a lifetime: the struct only *borrows* the instance's data
    (section 1.6), so making a `Problem` costs nothing.
  - **`const CACHE: bool`** is a *const generic*, a compile-time `true`
    or `false`. The compiler builds separate copies of every operator
    for `CACHE = true` and `CACHE = false`, so the `if CACHE` inside
    `candidate()` is decided at compile time and costs nothing at run
    time. This is the robustness factor "candidate distances from the
    cache or from the matrix".

  Its methods:
  - **`d(work, i, j)`** reads a distance and counts a matrix read. Every
    distance an operator reads goes through here, which is how the
    counts are exact (deviation D13).
  - **`candidate(work, lists, city, slot)`** gives candidate number
    `slot` of `city` and its distance: from the list's stored distances
    if `CACHE`, else from the matrix.
  - **`alpha_lists()`** returns the alpha lists, or crashes if there are
    none. Experiments check `Op::available` first, so the crash should
    never happen.
- **`reverse(tour, work, a, b)`**: reverse a path and count it. Every
  operator flips through this helper.
- **`exchange(tour, work, x1, x2, y1, y2)`** is the basic 2-opt move: remove
  edges (x1, x2) and (y1, y2), add (x1, y1) and (x2, y2). It is a single
  path reversal, with the direction worked out from whether `x2` comes
  after or before `x1`. The `debug_assert_eq!` checks in debug builds
  that `y2` sits after `y1` in the same direction, which the move needs.
  Or-opt, 3-opt and LK are all built as sequences of `exchange` calls.
- **`pick_anchor(n, rng, anchor)`**: the given anchor, or a random city.

**`enum Op`** lists the 16 operators.
- **Fixed discriminants.** `#[repr(u8)]` and the explicit values (`Swap =
  0`, `TwoOptFirst = 4`, …) pin each operator to its protocol number
  minus one, so O5 stays O5 even though O2, O3 and O4 were retired. The
  gaps are deliberate.
- **`RETIRED`**: the names of the five retired operators.
- **`Op::ALL`**: all current operators in protocol order.
- **`name()`**: the snake-case name used in every table.
- **`from_name(name)`**: the reverse lookup, or `None`.
- **`parse_stored(name)`**: for reading old tables. A current name gives
  `Some(op)`, a retired name gives `None` (skip the row), and anything
  else is an error.
- **`index()`**: position in `Op::ALL` (0 to 15), for arrays with one
  slot per operator. Do not use `op as usize` for that: it gives the
  protocol number with gaps.
- **`id()`**: `"O1"`, `"O5"`, … `"O21"`.
- **`deterministic()`**: whether the result depends only on the anchor
  (no random draws). Measurement A needs only one repeat per anchor for
  these. `matches!(value, A | B)` is shorthand for "is it one of these
  variants".
- **`family()`**: groups for analysis ("two_opt", "kick", or the name).
- **`available(problem)`**: O17 and O18 need alpha lists.
- **`apply(...)`**: one `match` that sends each variant to its function.
  O7 and O17 are the same function with different candidate lists, as
  are O16 and O18.

### 8.3 `ops/random.rs`: O1 swap

**`swap`**:
1. Pick the anchor `a` and a random other city `b` (a random offset
   along the tour from `a`).
2. Compute the change in length from exchanging their places. The
   formula has three cases: `b` right after `a`, `b` right before `a`,
   and the general case with four edges removed and four added.
3. Apply the exchange only if it shortens the tour.

It is the only random-move operator left. O2–O4 were retired because
they led almost no states.

### 8.4 `ops/scan.rs`: O5 two_opt_first

**`two_opt_first`**:
1. Take the edge from the anchor `a` to its successor `b`.
2. Walk around the whole tour, and for each other edge (c, d) compute
   the 2-opt gain of replacing (a, b) and (c, d) with (a, c) and (b, d).
3. Apply the first improving one; reversing the path `b..c` does it.

With no improvement it reads O(n) distances. That makes it the slow
full-scan reference against which the neighbour-list versions show
their speed-up.

### 8.5 `ops/two_opt.rs`: neighbour-list 2-opt (O7, O14, O15, O17)

- **`improve_city(tour, problem, lists, city, work)`** is the workhorse,
  also used inside kicks.
  - **The search.** For each direction (successor first, then
    predecessor), it removes the edge from `city` to its neighbour
    `partner`, then walks `city`'s candidate list.
  - **The stopping rule** (Johnson and McGeoch). A new edge from `city`
    can only help if it is shorter than the removed one, and the list is
    sorted by distance, so the walk stops at the first candidate at least
    as far away (`if added >= removed { break; }`).
  - **Applying the move.** For each candidate it computes the 2-opt gain;
    the first improving move is applied by one reversal.
  - **Return value.** The change and the four cities whose neighbours
    changed (the work queue of a descent needs them).
- **`two_opt_nl` (O7 and O17)**: `improve_city` at the anchor, once. O17
  passes the alpha lists instead of the 10-nearest lists.
- **`descend(tour, problem, work, start, stop_gain)`**: a full 2-opt
  local search with a work queue (the "don't-look bits" idea of Bentley
  1992).
  - **The queue.** It holds cities still worth looking at. Each city
    leaves the queue when its search fails. When a move succeeds, the
    four cities it touched go back on.
  - **Avoiding duplicates.** `queued` remembers who is on the queue, so
    no city is queued twice.
  - **Ending.** The loop ends when the queue is empty, i.e. at a 2-opt
    local optimum (with neighbour lists). With `stop_gain`, it stops
    early once it has gained that much; `states.rs` uses this to build
    "2-opt open" states that are partly descended.
  - **`VecDeque`.** A double-ended queue, cheap to push at the back and
    pop at the front.
- **`two_opt_descent` (O14)**: `descend` starting from the anchor alone.
  One application can make many moves.
- **`two_opt_targeted` (O15)**: look at the anchor and 7 random cities.
  Score each by "its longer tour edge minus the distance to its nearest
  neighbour", a measure of how badly connected it is, then apply O7 at
  the worst one.

### 8.6 `ops/or_opt.rs`: O9 or_opt_nl

- **`insert_segment(...)`**: moves a segment of 1–3 cities to sit between
  two adjacent cities `target` and `other`. It is built from two or three
  `exchange` (2-opt) calls, so it works in either direction of travel
  and on both layouts. The comments show the before → after picture of
  each case.
- **`or_opt_nl`**: for each direction and each segment size 1, 2, 3
  starting at the anchor:
  1. compute the gain of cutting the segment out (`removal`); skip if
     there is no gain;
  2. walk the anchor's neighbour list, stopping when the new edge is at
     least as long as `removal`;
  3. for each candidate `target`, try putting the segment next to it on
     either side.

  The first improving insertion is applied.

### 8.7 `ops/three_opt.rs`: O10 three_opt_nl

**`three_opt_nl`** tries `three_opt_at` from the anchor in both
directions. **`three_opt_at`** is a Lin–Kernighan search cut off at
depth 3, in LK's notation:

1. **Depth 1.** Remove (t1, t2). For each candidate t3 of t2 with
   positive partial gain `g1 = d(t1,t2) − d(t2,t3)`:
2. **2-opt closure.** If t4 is t3's predecessor, closing with (t4, t1) is
   a 2-opt move; apply it if it improves.
3. **Sequential 3-opt, first case.** Otherwise go one level deeper: pick
   t5 from t4's candidates, choose t6 so the result is still one cycle,
   and check whether closing with (t6, t1) improves.
4. **Second case.** t4 can also be t3's successor. Then t5 must lie
   between t2 and t3, and the move is applied as three exchanges.

The `between` closure answers "is `middle` on the path from `first` to
`last` in the search direction?". `succ` and `pred` are closures that
follow the chosen direction, so the same code serves both directions.

### 8.8 `ops/lk.rs`: O16 and O18, a Lin-Kernighan step

**The idea.**
1. **Start.** Remove the edge (t1, t2) at the anchor.
2. **Grow the move.** Repeatedly add an edge from the current end
   ("last") to a candidate t3, remove the edge (t3, t4) that keeps the
   tour a single cycle, and check whether closing with (t4, t1) would
   give a shorter tour. Each step is one 2-opt flip, so the move grows
   one flip at a time.
3. **Commit or backtrack.** Commit the first closing that improves. If
   none does, explore the most promising alternatives: 4 at level 1, 3
   at levels 2 and 3, 2 at level 4, then 1 (`LK_BREADTH`). Go up to 25
   flips deep (`LK_DEPTH`), and undo dead ends.

These are Applegate, Cook and Rohe's settings.

**The code.**
- **`lin_kernighan`**: picks the anchor and calls `lk_step`.
- **`lk_step`**: for each direction, sets up a `Search` and calls
  `extend` at level 1. The O21 kick also uses it.
- **`struct Search`**: the search state:
  - the problem and lists;
  - `t1`;
  - the edges added so far (`added`; an added edge may never be removed
    again, a classic LK rule);
  - the work counter.

  It has two lifetime parameters, `'p` and `'w`, because it borrows two
  things that live for different lengths of time.
- **`edge(a, b)`**: an edge as an ordered pair, so (3, 7) and (7, 3)
  compare equal.
- **`extend(tour, last, gain, level)`**: the recursive heart.
  1. For each candidate t3 of `last` with positive partial gain, skip the
     illegal ones. Compute t4 and the gain if the tour were closed now.
  2. If closing improves, apply the flip and return the gain.
  3. Otherwise remember `(gain so far, t3, t4)` as an option.
  4. After the loop, sort the options (largest gain first) and try the
     best `breadth` of them: mark the tour, apply the flip, recurse one
     level deeper. If the recursion fails, undo to the mark.

  The `self.work.flips += 1` on undo counts the undo like the Python
  code's explicit reverse move, so the counts stay comparable.

### 8.9 `ops/kicks.rs`: perturbations repaired by local search

- **`segment_kick(...)`** (shared by O8 and O21):
  1. Read the tour from the anchor's successor as three pieces X Y R (R
     ends with the anchor). The two cut points are random.
  2. Rearrange to R Y X, i.e. swap the order of X and Y. That replaces
     three edges: a "segment exchange", a sequential 3-opt move. It is
     done with three reversals: reverse X, reverse Y, then reverse the
     two together.
  3. Return the length change and the six cities at the cuts, which
     start the repair.
- **`keep_if_shorter(tour, work, mark, before)`**: if the tour is now
  shorter than `before`, keep it; otherwise undo everything since `mark`.
  Every trial ends this way (deviation D5: Python built the kick on a
  copy of the array instead).
- **`kick` (O8)**: segment kick at the anchor, then a 2-opt `descend` from
  the six touched cities, kept only if shorter.
- **`double_bridge_move(tour, problem, work, cuts)`**: the classic double
  bridge (Martin, Otto and Felten 1991). With four cut points, the tour
  S0 S1 S2 S3 becomes S0 S3 S2 S1. This is a 4-opt move that local
  search cannot easily undo, which is why it is the standard ILS kick.
  It is done with four reversals and checked against Concorde's
  `random_four_swap` in `tests/external.rs`.
- **`double_bridge_trial`**: double bridge, 2-opt descent from the eight
  cut cities, keep if shorter.
- **`in_tour_order(tour, anchor, others)`**: sorts three cities by their
  distance along the tour from the anchor, so the four cuts come in tour
  order.
- **`double_bridge` (O12)**: the anchor plus three random cities.
- **`double_bridge_local` (O13)**: the anchor plus three random cities
  from its neighbour list, so the kick is local. It disturbs one small
  region instead of the whole tour.
- **`lk_kick` (O21)**: chained LK, the strongest operator in E1R.
  1. Segment kick.
  2. Repair: a queue of touched cities, each given an LK step (alpha
     lists if available). Cities an improving step touches re-enter the
     queue (`touched_since`).
  3. Keep if shorter.

### 8.10 `ops/recreate.rs`: ruin and recreate (O11, O19)

**The idea.** Remove some cities and reinsert each at the cheapest place
nearby.

**The overlay.** Most trials are rejected, so the trial does not touch
the real tour at all. It works on an `Overlay`, a short list of changed
"successor" and "predecessor" links on top of the unchanged tour.
- `link(left, right)` records a new link.
- `succ`/`pred` look up the newest override first, falling back to the
  tour.

A rejected trial costs only the work of the search. An accepted one
rebuilds the tour order in O(n) (`tour.replace`), which is logged so it
can be undone (deviations D6, D7).

- **`ruin_and_recreate(tour, problem, rng, work, ruined, blink)`**:
  1. **Remove.** Remove each ruined city by linking its predecessor to
     its successor, and add up the length change.
  2. **Shuffle** the removed cities.
  3. **Reinsert** each one at the cheapest edge next to one of its
     neighbour-list cities that is back in the tour. "Blink" (O19 only)
     skips each candidate position with probability 1%, a small dose of
     randomness from SISR.
  4. **Fallback.** If none of its neighbours is in the tour, scan every
     edge.
  5. **Keep only if shorter.** If the total change is negative, write the
     new order into the tour.
- **`ruin_recreate` (O11)**: ruin the anchor and its 9 nearest neighbours
  (radial ruin, Schrimpf et al. 2000).
- **`string_removal` (O19)**: SISR-style (Christiaens and Vanden Berghe
  2020), adapted to one tour.
  1. Remove 1 to 3 strings of 1 to 10 consecutive cities each.
  2. The first string contains the anchor, at a random offset.
  3. Each further string starts at the next of the anchor's neighbours
     not yet removed.
  4. Reinsert with blinks.

  The constants `STRING_MAX`, `STRING_COUNT` and `STRING_BLINK` are the
  SISR parameters.

---

## 9. `src/timing/`: the clock and the machine

The study's ground rule is that time is always counted. An operator that
gains 10 units in 2 µs beats one that gains 15 in 5 µs. So the timing
has to be right at the level of microseconds, which is far below what
ordinary timers measure reliably. This folder holds everything that makes
that possible. It is also the only place with `unsafe` code.

The main enemies of precise timing, and the file that fights each one:

| Problem | Effect | File |
|---|---|---|
| Ordinary timers are slow and coarse | A system call per measurement costs more than a cheap operator | `tsc.rs` |
| The CPU changes its frequency (boost, power saving) | The same work takes different time | `machine.rs`, `cycles.rs` |
| Two hyper-threads share one physical core | A task on the sibling slows the timed thread by up to half | `pin.rs`, `disturb.rs` |
| The thread gets interrupted or preempted | One sample is much longer | `disturb.rs` |
| Memory handed back to the OS and faulted in again | A page fault inside a timed region | `disturb.rs` |
| A core that was idle starts slow | The first sample after a pause is slower | `disturb.rs` |

### 9.1 `timing/tsc.rs`: the timestamp-counter clock

**What.** Modern x86 CPUs have a counter, the TSC (time-stamp counter),
that ticks at a fixed rate whatever the core's actual speed. Reading it
takes a single instruction (`rdtsc`), about 10–30 ns. This file uses it
as the clock.

- **`start()`** reads the counter as `lfence; rdtsc; lfence`. CPUs run
  instructions out of order. The fences stop earlier instructions from
  leaking past the read, and the region's instructions from starting
  before it. The `unsafe` block calls CPU instructions directly (Rust
  "intrinsics"); its `// SAFETY:` comment explains why that is fine.
- **`stop()`** reads with `rdtscp`, which waits for all earlier
  instructions to finish, then fences again.
- **`monotonic_raw_ns()`** reads the operating system's raw monotonic
  clock, in nanoseconds. It is slower but trustworthy, and serves as the
  reference for calibration.
- **`require_invariant_tsc()`** refuses to run unless `/proc/cpuinfo`
  shows the flags `constant_tsc` (fixed rate), `nonstop_tsc` (keeps
  ticking in sleep states) and `rdtscp`.
- **`gcd(a, b)`**: greatest common divisor, used to find the counter's
  step.
- **`struct Clock`**: the result of calibration.

  | Field | Meaning |
  |---|---|
  | `ticks_per_ns` | Ticks per nanosecond |
  | `calibration_spread` | How much the calibration windows disagreed |
  | `overhead_ticks` | Mean cost of an empty timed region; subtracted from every measurement |
  | `overhead_outliers` | Interrupted empty regions left out |
  | `overhead_median_ticks` | Median empty region, for comparison |
  | `step_ticks` | The counter's step size |

  On the Ryzen the counter advances in steps of 40 ticks (10 ns), not 1,
  so very short regions are quantised.
- **`Clock::calibrate(windows, window_ms)`** does three things.
  1. **Frequency.** For each window (for example 5 × 200 ms), it
     busy-waits while reading both the TSC and the OS clock, and divides
     ticks by nanoseconds. It takes the median, and fails if the windows
     disagree by more than 0.1%.
  2. **Overhead.** It times 100,000 empty regions and takes the mean,
     leaving out any longer than 4× the median, which were interrupted.
     The comment explains why the mean and not the median: with 40-tick
     steps the median is off by up to a step, and rates need the mean.
  3. **Step.** It takes the gcd of the gaps between consecutive reads.
- **`overhead_ns()`, `ns(ticks)`, `region_ns(begin, end)`**: conversions.
  `region_ns` subtracts the overhead. It can be slightly negative for
  regions shorter than the overhead's noise; averaging takes care of
  that.

### 9.2 `timing/cycles.rs`: has the core slowed down?

**What.** The TSC ticks at a fixed rate, so if the core slows down (heat,
power limits), the measured time rises but nothing in the timing itself
says why. This file counts **core cycles** with the kernel's
performance-counter interface (`perf_event_open`, via the `perf-event2`
crate). Cycles divided by elapsed run time is the average real frequency.

- **`open_event(event)`**: opens and enables one counter for the
  calling thread.
- **`struct ThreadCounters`**: two counters, user-space cycles and the
  "task clock" (nanoseconds the thread actually ran).
  - `open()` opens both.
  - `read()` reads both into a **`Reading`**.
- **`Reading::khz_since(earlier)`**: average frequency between two
  readings, or `None` if the thread did not run. `checked_sub` returns
  `None` instead of wrapping on underflow.
- **`reference_khz(ms)`**: the frequency of a short busy loop, the
  reference for later checks.
- **`drifted(reference, khz, tolerance)`**: whether a stretch of work ran
  more than `tolerance` away from the reference. Measurement A flags such
  states as suspect.

The comment notes one limit. Without root, only user-space cycles can be
counted, while the task clock also includes kernel time, so the computed
frequency is a slight underestimate. The timing loops make almost no
system calls, so the effect is small.

### 9.3 `timing/counters.rs`: instructions and cycles per timed region

**What.** Each timed region is also *counted*: instructions retired and
core cycles, read directly from the CPU with `rdpmc`, with no system call
in between.

**Why.** The instruction count of the same work is the same every time,
whatever else the machine does. That makes it a machine-independent
second measure of an operator's work, and a way to detect disturbed
samples: if time is far above cycles, the thread was interrupted. The
comment is careful to say it is *not* a substitute for time, since time
per instruction varies fourfold within one operator.

- **`struct Counts`** holds instructions and cycles as floats, with small
  helpers: `scaled(factor)` multiplies, `minus(other)` subtracts, and
  `mean_with(other)` averages two counts (used for the median of an even
  number of samples). `#[must_use]` makes the compiler warn if you call
  one and ignore the result.
- **`struct Reading`**: raw counter values at one moment.
- **`OVERHEAD`**: a process-wide value, computed once, of what an
  *empty* region counts. `OnceLock` is a box that can be filled exactly
  once, safely from any thread. Why one value for the whole process: when
  each thread measured its own, two threads differed by 53 instructions,
  enough to fake a 12% change for a cheap operator.
- **`struct Counters`** holds two `Sampler`s, the perf-event objects that
  can be read with `rdpmc`.
  - `open()` creates them "pinned", so the kernel never swaps them out:
    a counter that is time-shared cannot be read with `rdpmc`. It then
    measures the overhead over 101 empty regions if nobody has yet,
    using the minimum for instructions and the median for cycles.
  - `read()` reads both.
- **`thread_local! { static COUNTERS ... }`**: each thread gets its own
  copy of this variable. Counters count only the thread that opened
  them, so each thread needs its own. The type `RefCell<Option<Option<
  Counters>>>` reads as follows: `RefCell` allows changing it through a
  shared reference; the outer `Option` means "not tried yet"; the inner
  means "tried, and opening worked or failed".
- **`with_counters(f)`**: opens the counters on first use (logging one
  warning if that fails) and runs `f` with them.
- **`read()`**: the counters now; call it just before `tsc::start`.
- **`between(before, after)`**: the counts of a region, minus the
  overhead.
- **`check()`**: whether counters can be opened at all, for the machine
  report.

### 9.4 `timing/pin.rs`: choosing and pinning cores

**What.** A timed thread must run on one fixed CPU (otherwise the OS
moves it around, losing caches) and must not share a physical core with
other busy work.

- **`allowed_cpus()`**: the logical CPUs this process may use (its
  "affinity mask"; `taskset` can restrict it).
- **`siblings(cpu)`**: the logical CPUs sharing `cpu`'s physical core,
  read from `/sys/devices/system/cpu/cpuN/topology/thread_siblings_list`
  (a text like `0,8` or `0-1`).
- **`physical_cores()`**: one logical CPU per physical core, sorted
  quietest first, by how many interrupts each has handled since boot.
  Experiments time on the first (quietest) core and put their untimed
  main thread on the last. The comment records why: on 2026-11-02, CPU 0
  was busier with timer ticks and USB interrupts, and ran 3% slower for
  ten minutes.
- **`interrupts_per_cpu()`**: parses `/proc/interrupts`. The first line
  names the CPU columns, and each later line gives counts per CPU. It
  returns a total per CPU in a `BTreeMap`. Lines with a single count
  (machine-wide, such as `ERR`) are skipped.
- **`pin_current_thread(cpu)`**: restricts the calling thread to one
  CPU.

### 9.5 `timing/machine.rs`: is the machine locked for timing?

**What.** It reads the settings that change CPU speed and reports which
of them are not in the "locked" state. The tool never changes them
itself (that needs root); `tools/lock-clock.sh` does.

**Readers.** Each reads one file and returns `None` if it cannot.
- `read_trimmed(path)`: read a small file and trim whitespace.
- `cpu_flags()`, `cpu_model()`: from `/proc/cpuinfo`, via the `procfs`
  crate.
- `kernel()`: the kernel version.
- `boost()`: whether turbo boost is on (`/sys/.../cpufreq/boost`).
- `governor(cpu)`: the frequency policy, which must be `performance`.
- `frequency_limits(cpu)`: the floor and ceiling the governor may use.
  The floor must equal the ceiling, or a core slows down whenever it
  idles briefly.
- `average_khz(cpu)`: the kernel's recent frequency estimate.
- `temperature_c()`: package temperature from the AMD or Intel sensor
  driver.
- `soc_clock_mhz(clock)` and `soc_power_level()`: on an APU (a CPU with a
  built-in GPU, like the Ryzen 7940HS), the memory and "data fabric"
  clocks belong to the GPU driver and follow load unless its power level
  is forced to `high`. E0 found that memory-bound timings depended on
  them.
- `on_mains()`: mains power or battery.

**`struct MachineState`** is one snapshot of all of these, for the CPUs
in use; `#[derive(Serialize)]` lets it be written as JSON into every run
directory.
- **`read(cpus)`** takes the snapshot.
- **`problems()`** lists every way the snapshot is not "locked":
  - boost on or unknown;
  - governor not `performance`;
  - floor below ceiling;
  - GPU power level not `high`;
  - SMT on;
  - running on battery.

  `if let Some(level) = &x && level != "high"` is a newer Rust form that
  combines "if it exists" and "and it meets a condition".

**`require_locked(cpus, allow_unlocked)`**: called before every timed
stage. It collects `problems()` plus whether the cycle and instruction
counters can be opened. If there are problems and `--allow-unlocked-clock`
was not given, it stops with an error listing them. Otherwise it logs
them and returns them, so they go into the run's provenance and anyone
reading the results later can see the timing was done on an unlocked
machine.

### 9.6 `timing/disturb.rs`: catching disturbed samples

**What.** Even when pinned, a timed thread can be disturbed: another task
can preempt it, a task can run on its SMT sibling (sharing the core's
resources), or it can take a page fault. This file detects all three for
each sample, so the sample can be taken again.

- **`struct Mark`** is a snapshot of counters that only grow:
  - the thread's context switches and page faults (from `getrusage`);
  - how many time slices and nanoseconds tasks have run on the sibling
    CPU (from `/proc/schedstat`).

  Any user can read both, no root needed.
- **`struct Watch`** keeps `/proc/schedstat` open and knows the sibling
  of the current CPU.
  - `for_current_cpu()` builds one.
  - `now()` takes a `Mark`.
  - `disturbed(before, sample_ns)` decides whether a sample was
    disturbed. It was, if the thread switched or faulted. Otherwise, for
    a short sample (under 200 µs), any activity on the sibling counts;
    for a longer one, the sibling must have run for more than 0.5% of the
    sample.
  - `sibling_counters()` parses the sibling's line of `/proc/schedstat`.
- **`sibling_of(cpu)`**: the other logical CPU on the same core.
- **`thread_usage()`**: context switches and page faults of the calling
  thread.
- **`keep_heap()`**: tells the C memory allocator never to give freed
  memory back to the OS (`mallopt`, the second `unsafe` use). Otherwise
  operators that allocate a little memory per application (kicks, LK)
  would sometimes page-fault inside a timed region; E0 saw 35% variation
  from this. `main()` calls it first thing.
- **`spin_up(milliseconds)`**: keeps the thread busy (a pointless
  arithmetic loop) for 50 ms before each timed block. After an idle
  pause, a core's first work runs slow; a probe found 1.57× slower in the
  first millisecond. `black_box` stops the compiler from deleting the
  loop as useless.
- **`with_watch(f)`**: a per-thread `Watch`, recreated if the thread has
  moved to another CPU.
- **`count_retake()`, `take_retakes()`**: count how many samples were
  retaken, for the run report. `std::mem::take` reads a value and leaves
  a zero behind.

---

## 10. `src/io/`: run directories, Parquet, provenance

Every experiment writes into a **run directory** under `runs/`. It holds:
- `config.json`: the settings and provenance;
- Parquet tables of results;
- log and summary files.

Python reads these to draw figures. This folder has the shared helpers.

### 10.1 `io/rundir.rs`

- **`new_run_directory(root, experiment, unlocked)`**: creates
  `runs/<experiment>-rs/<YYYYmmdd-HHMMSS>/`. Runs on an unlocked machine
  get the suffix `-unlocked` in the folder name, so their timings can
  never be mistaken for locked ones. It refuses if the folder already
  exists.
- **`timestamp()`**: the local time as `20261105-140622`.
- **`write_atomic(path, contents)`**: write to `<path>.tmp`, then rename.
  A rename is atomic on Linux: other programs see either the old file or
  the complete new one, never half a file. Used for everything a crash
  could otherwise corrupt.
- **`temporary_path(path)`**: `<path>.tmp`.

### 10.2 `io/provenance.rs`

**What.** Everything a later reader needs to know about how a run was
made, written into `config.json`.

- **`struct Build`** and the constant **`BUILD`**: the values that
  `build.rs` baked in (source digest, `Cargo.lock` digest, compiler,
  profile, optimisation level, flags). `env!("AOS_SOURCE_SHA256")` reads
  a compile-time environment variable, so these are fixed constants in
  the binary.
- **`git(repository, arguments)`**: runs a git command and returns its
  output, or `None` if git fails. `.then(|| ...)` turns a `bool` into an
  `Option`: `Some(...)` if true, `None` if false.
- **`git_state(repository)`**: the commit hash and whether tracked files
  have uncommitted changes ("dirty"). `json!({...})` builds a JSON value
  with Python-dict-like syntax.
- **`host()`**: CPU model, the CPU flags that matter for timing, kernel
  version and number of logical CPUs.
- **`provenance(repository)`**: build, git, host and the random-number
  scheme (`rng::SCHEME`) together.
- **`write_config(directory, repository, settings)`**: writes
  `config.json` as `{ "provenance": ..., "settings": ... }`.

### 10.3 `io/parquet.rs`

**What.** Parquet is a compressed, typed, column-oriented table format,
fast to read from pandas or polars. This file wraps the `arrow` and
`parquet` crates in a small, simple interface.

- **`enum Column`**: one column of values. The six kinds:
  - `I64`: 64-bit integers;
  - `U32`: city indices;
  - `F64`: floats;
  - `OptF64`: floats with missing values;
  - `Bool`: flags;
  - `Str`: labels.

  `len()` gives the row count; `into_array()` converts to Arrow's array
  type. `Arc::new(...)` wraps it so Arrow can share it without copying.
- **`struct Table`**: a list of `(name, column)` pairs.
  - `Table::new().with("instance", Column::Str(names)).with("gain",
    Column::I64(gains))` builds a table in "builder style": each `with`
    takes the table, adds a column and hands it back.
  - `into_batch()` checks all columns have the same length (a loud error
    otherwise) and makes an Arrow `RecordBatch`.
- **`properties()`**: write with zstd compression.
- **`write_batch(path, batch)`, `write(path, table)`**: write atomically
  (temporary file, then rename).
- **Parts, for long runs.** A Parquet file is only readable once its
  footer is written at the very end, so a run killed halfway through one
  big file loses everything. Long runs therefore write one small "part"
  file per finished unit (for example per state) into `<table>.parts/`,
  and merge them at the end.
  - `parts_directory(directory, name)`: `<directory>/<name>.parts`.
  - `write_part(directory, name, key, table)`: write
    `<name>.parts/<key>.parquet`. **The existence of a part means the
    unit is done**, which is how stopped runs resume: they skip units
    whose part exists.
  - `has_part(directory, name, key)`: checks for one.
  - `merge_parts(directory, name)`: reads all parts in key order (sorted
    file names) and writes `<name>.parquet`. It fails if the parts have
    different columns.
- **`read(path)`**: reads a whole Parquet file into one batch.
- **`column_i64`, `column_f64`, `column_str`, `column_bool`**: pull one
  named column out of a batch as a plain `Vec`. They fail loudly if the
  column is missing, has the wrong type or (for most) has missing values.
  `column_f64` turns missing values into NaN instead. `downcast_ref` is
  how you ask "is this generic array actually a `Float64Array`?".
  `column_i64` accepts both `Int64` and `UInt32` columns.
- **`write_csv(batch, columns, out)`**: what `aos show` uses; it prints
  the chosen columns (or all) as CSV.

Tests check that parts merge in key order and that a table with unequal
column lengths is refused.

---

## 11. `src/experiments/`: the experiments

Each experiment is one file. They share helpers in `mod.rs` and
`common.rs`, which are described first. The big four (`e0.rs`,
`marginal.rs`, `population.rs`, `rollouts.rs`) are long, so for each the
guide first gives the stages, then walks through the functions in file
order.

### 11.1 `experiments/mod.rs`: generated instances and statistics

- **`UNIFORM_SIDE = 1_000_000`**: coordinates lie in [0, 10⁶), as in
  DIMACS `portgen`.
- **`uniform_instance(n, seed, with_alpha)`**: a uniform random instance
  made inside the program, with integer coordinates from a seeded stream.
  E0 uses it to measure costs against `n` at any size, without needing
  files. Its "SHA-256" is computed from the coordinates, so identical
  inputs give an identical identity. It optionally computes alpha lists.
- **`random_order(n, seed, label)`**: a random tour, seeded.
- **Statistics**, thin wrappers over the `statrs` crate:
  - `median`;
  - `quantile(values, q)`: Hyndman and Fan definition 8, R's
    `type = 8`. The doc notes it differs slightly from NumPy's default
    for few values;
  - `mean`;
  - `standard_deviation` (with n − 1);
  - `coefficient_of_variation` (standard deviation / mean);
  - `cv_interval(cv, count)`: a 95% interval for a coefficient of
    variation, from the chi-square distribution.

  E0's repeatability gate reports CVs with these intervals. Each panics
  on empty input, because a statistic of nothing is a bug.

### 11.2 `experiments/common.rs`: what the experiments share

**Names and files.**
- **`CLASSES`**: the four local-optimality classes in search order: "2-opt
  open", "2-opt optimum", "3-opt optimum", "LK optimum". A state's class
  comes from measurement A: which local searches still improve it.
- **`STATE_NAMES`**: the 12 E1 states in building order: `random`,
  `nearest_neighbour`, `d25`, `d50`, `d75`, `d90`, `opt2`, `oropt`,
  `opt3`, `lkopt`, `ils1`, `ils2`.
- **`EARLY_STATES`**: `random` and `nearest_neighbour`, which budgeted
  runs skip on large instances (every operator would run over a hugely
  defective tour).
- **`state_rank(name)`**: the position in that list, for sorting.
- **`instance_family(name)`**: the family from the name prefix
  (`portgen-` → "uniform", `netgen-` → "clustered", …, else "TSPLIB").
- **`struct StateRecord`** and **`struct StatesFile`**: the JSON format of
  states files. One file holds the states of one instance and seed: name,
  gap, length, how it was made, the tour, and whether it duplicates an
  earlier state. The old Python builder used the same format, so either
  can feed measurement A.
- **`read_states_directory(directory)`**: reads every `.json` file in a
  folder, sorted by name.
- **`load_instance(repository, path)`**: load an instance and make sure
  its alpha candidates exist.

**Implementation choices.**
- **`enum Storage`**: `Matrix` or `Computed`.
- **`enum LayoutChoice`**: `Auto`, `Array` or `TwoLevel`.
- **`struct Implementation`**: storage, `cache` (candidate distances from
  the lists or the matrix), layout and `n_split`. The default is the main
  design (matrix, cache on, auto, 1000).
  - `two_level(n)`: whether `n` cities use the two-level list.
  - `layout_name(n)`: its name.

  Measurement A's robustness factors change one of these.

**`trait Job` and `dispatch`: from run-time options to compiled code.**
This is the cleverest bit of plumbing, and the reason the experiments
can choose storage, cache and layout from the command line while the
operators stay fully compiled.

- **The problem.** The operators are generic: there is a separately
  compiled copy for each combination of `D` (storage), `C` (cache) and
  `L` (layout), 2 × 2 × 2 = 8 combinations. But the command-line options
  are only known at run time.
- **`trait Job`.** A piece of work (for example "measure this state") is
  written as a struct implementing `Job`, whose generic `run::<D, C,
  L>(...)` does the work.
- **`dispatch(job, instance, implementation)`.** It looks at the options
  and calls the right compiled copy, in three steps:
  1. `dispatch` picks the storage;
  2. `with_cache` builds the `Problem` with `CACHE = true` or `false`;
  3. `with_layout` picks `Array` or `Blocks`.

  The choice is made once per job, not once per distance read.

**Other helpers.**
- **`tour_of(instance, order)`**: a `Tour` in a chosen layout.
- **`parallel_map(items, workers, name, job)`**: runs `job` on every item
  on a rayon pool of `workers` threads and returns the results in item
  order. If an item fails, the error is labelled with the item's `name`.
  Untimed work only: timed work must stay on a pinned core.
- **`all_cpus()`**: the number of logical CPUs available.

**Batched timing (the core of measurement A and E0).** Timing one
application of a cheap operator (a microsecond or less) on its own is
dominated by the clock's own overhead and noise. So the code times
**batches**: one timed region around applying the operator at every
anchor in turn, undoing each application inside the region. The undo is
part of the timed work, so it must be measured and subtracted. Three
"passes" handle this:

- **`enum Pass`**:
  - `PerCall`: one region per application, which gives per-anchor times;
  - `Batch`: one region around all anchors;
  - `Restore`: one region around *replaying and undoing* every
    improving application's changes, which costs about two restores per
    application.

  **`Pass::order(improves, rng)`** shuffles the passes, so no pass always
  runs second with warm caches. `Restore` is only needed if something
  improved.
- **`warm_up(...)`**: applies the operator at every anchor once, untimed,
  before the timed passes, so the operator's code and data are in cache,
  as they would be in a real search. Without it, E0 saw the first pass
  run up to four times slower.
- **`timed_batch(...)`**: one batch region. It reads the counters and the
  clock, then for each anchor: mark, apply, record the delta, undo. It
  then reads the clock and counters again and returns a `Sample`. Each
  anchor gets its own fresh random stream, so the batch is
  reproducible.
- **`struct Sample`**: nanoseconds plus optional instruction and cycle
  counts.
- **`sampled(region)`**: times a region several times and returns the
  median.
  - **How many samples.** A region of 1 ms or more is timed once.
    Anything shorter is timed at least 3 times and up to 9, until the
    samples add up to 1 ms.
  - **Retakes.** If the disturbance watch says another task interfered
    (section 9.6), the whole set is retaken, up to 10 times.
  - **Why the median.** E0 found that about one repeat in five of a
    microsecond batch had an outlier; the median removes it.
- **`median_of(samples)`**: the middle sample by time (or the mean of the
  middle two).
- **`sampled_batch(...)`**: `timed_batch` under `sampled`. Each sample
  starts from a normalised storage (`tour.normalize()`). It also checks
  that every sample produced exactly the same deltas, and fails loudly if
  not: an operator that is not reproducible would make the timing
  meaningless.
- **`improving_replays(...)`**: for each anchor, apply, record a
  `Replay` if the application improved, and undo (untimed). This is the
  input to the `Restore` pass.
- **`timed_restores(tour, replays, clock)`**: the `Restore` pass itself.
- **`batched_time(single, restores, applications)`**: the arithmetic.
  - restore time = restore batch / 2;
  - time per application = (batch − restore time) / applications.
- **`batched_counts(...)`**: the same arithmetic for instruction and
  cycle counts.

### 11.3 `experiments/states.rs`: building E1 states (`aos states`)

**What.** For each instance and seed, it builds a sequence of tours
along one search path, from terrible to very good. These are the
"states" whose operator rates measurement A measures.

| State | How it is made |
|---|---|
| `random` | A random permutation |
| `nearest_neighbour` | Nearest-neighbour tour from city `seed mod n` |
| `d25`, `d50`, `d75`, `d90` | The NN tour after a 2-opt descent stopped at 25/50/75/90% of the full descent's gain |
| `opt2` | The full 2-opt (O7) descent |
| `oropt` | VND with O7 and O9 (Or-opt) from `opt2` |
| `opt3` | VND adding O10 (3-opt) |
| `lkopt` | VND adding the LK step (O18 if alpha lists exist, else O16) |
| `ils1`, `ils2` | `lkopt` after n, then 4n more O8 kick trials, each round ending with the LK VND |

Duplicate tours (for example when `oropt` finds nothing beyond `opt2`)
are kept but marked `duplicate_of`, and measurement A skips them.

- **`FRACTIONS`, `ILS_ROUNDS`**: the constants of the table.
- **`struct Settings`**: instances, seeds, the size above which only the
  first seed is used, and workers.
- **`nearest_neighbour_tour(matrix, start)`**: from `start`, repeatedly go
  to the closest unvisited city (ties to the smaller index). It is O(n²).
- **`vnd(tour, problem, visit, ops)`**: variable neighbourhood descent.
  Visit every city in a fixed random order; at each, try O7 and then each
  operator in `ops`. Repeat full passes until a whole pass improves
  nothing. `clear_log` after each city stops the undo log from growing
  forever.
- **`build(instance, path, seed)`**: makes all the states in order with
  seeded streams. It uses a closure `add` that checks the tour
  (`tour.check`), detects duplicates and records the state.
  - **The closure.** It is `let mut add = |...| { ... }` because it
    changes the captured `states` vector.
  - **The partial descents.** `d25`…`d90` reuse `descend` with
    `stop_gain`.
- **`run(repository, settings, out)`**: makes the run directory, writes
  `config.json`, and builds every (instance, seed) in parallel with
  `parallel_map`, largest instances first so the longest tasks don't
  finish last.
  - **Resuming.** An existing output file is skipped, so a stopped run
    resumes.
  - **Output.** Each file is written atomically as
    `states/<name>_s<seed>.json`.

### 11.4 `experiments/compare.rs`: comparing two measurement-A runs (`aos compare`)

**What.** For the robustness factors (main design against one changed
implementation choice) or two nights, it asks per state:
- is the leader (best operator) the same on both sides?
- is the class the same?
- what is the ratio of the rates of the main side's top three
  operators?

A leader that changes with the implementation is flagged as
implementation-dependent.

- **`type Cells`**: a map from (instance, seed, state) to (class, rates
  per operator).
- **`read_rust(directory)`**: reads `cells.parquet` into that map.
  `.entry(key).or_insert_with(...)` is the idiom for "get the entry,
  creating it first if missing".
- **`leader(rates)`**: the operator with the highest rate (ties broken
  by name).
- **`compare(main, alternative, out)`**: for each state in both runs,
  restrict to operators measured on both sides, find both leaders, and
  record whether the leader changed. A change only counts if the main
  leader's rate is above zero: a state where nothing gains has no real
  leader. It writes a Parquet table and a Markdown summary with counts per
  class, and prints the summary.

### 11.5 `experiments/marginal.rs`: measurement A (`aos measure`)

**What it measures.** For every state and every operator, the
**marginal rate**: the mean gain per application divided by the mean time
per application, in length units per second. "Marginal" means the rate
of one more application from this state, not of a whole run. The state's
**leader** is the operator with the highest rate.

**The three stages.** You can run them separately with `--stage`, or all
at once.

1. **gains** (untimed, all CPUs):
   - For each state, find its exact local-optimality class (which local
     searches still improve it).
   - Choose the anchors (every city, or a sample sized so the timing fits
     the budget).
   - Apply every operator at every anchor, deterministic ones once,
     random ones `repeats` times, each from its own stream. Record the
     gain, counted work and resulting tour hash.
2. **times** (timed, pinned cores, nothing else running): time the same
   applications. Each replay must reproduce the gain (and tour hash) of
   stage 1 exactly, or the run stops. That guarantees the times and the
   gains belong to the same applications.
3. **summarise** (untimed, all CPUs): rates with bootstrap intervals per
   (state, operator), and the leader per state.

**Why gains and times are separate.** Gains need many repeats and all
CPUs; times need one quiet pinned core. Separating them lets the slow,
untimed part use the whole machine.

**Resuming.** Every stage skips a state whose part file already exists,
and a run directory remembers its settings. A later stage refuses to run
with different settings.

#### Settings and the list of states

- **`PILOT_ANCHORS = 6`, `PASSES_PER_ROUND = 3`, `REFERENCE_MS = 200`**:
  constants for sizing anchors and the frequency check.
- **`struct Settings`**:
  - `states`: the states folder;
  - `repeats`: for random operators;
  - `seed`, `bootstrap`;
  - filters: `instances`, `seeds`, `stages`;
  - `state_seconds`: the timing budget per state; `None` means every
    anchor;
  - `min_anchors`;
  - `early_max_n`: skip early states above this size;
  - `min_rounds`, `max_rounds`, `precision`: when to stop timing;
  - `implementation`: the robustness factors;
  - `anchors_from`: reuse another run's anchors, so a robustness
    comparison measures the same anchors.
- **`Settings::full`, `budgeted`, `quick`**: presets. `..Self::full(states)`
  means "everything else as in `full`". `budgeted` (the E1 main-run
  design) uses 32 repeats, 1 s per state, and skips early states above
  1,000 cities.
- **`gain_repeats(op)`**: 1 for deterministic operators, else `repeats`.
- **`struct StateTask`**: one state to measure (instance path and name,
  `n`, seed, the state).
  - `label()` is `"kroA100:s0:opt2"`, used in random streams.
  - `key()` is `"kroA100_s0_opt2"`, used in file names.
- **`select_tasks(repository, settings)`**: reads the states folder,
  applies the filters, drops duplicates, and sorts the states. The largest
  instances go first and early states before late ones, so the slowest
  work starts first.
- **`resolve`**: repository-relative paths.
- **`operators()`**: all 16 operators.

#### Stage 1: gains

- **`struct Flags`**: whether O7, O5, O9, O10 and the LK step improve
  anywhere in the state.
- **`Flags::class()`**: the class index:
  - 0 "2-opt open": O7 or O5 improves;
  - 1 "2-opt optimum": else, O9 or O10 improves;
  - 2 "3-opt optimum": else, LK improves;
  - 3 "LK optimum": nothing improves.
- **`flags(tour, problem)`**: computes the flags exactly, trying every
  city as anchor and stopping at the first improvement. Each check works
  on a fresh copy of the tour, because the first improvement changes it.
- **`classify(tour, problem)`**: the same class, faster. It stops as soon
  as the class is known, and undoes instead of copying. E1R and the
  population runs use it on tours found during a run.
- **`struct GainMeta`**: what stage 1 stores per state besides the
  applications:
  - the anchors;
  - whether they are a sample;
  - the flags;
  - the stage's wall time;
  - the layout;
  - the tour length;
  - `round_ns_estimate`: an untimed estimate of one timed round, used for
    sizing.
- **`struct GainRows`**: one row per application, with these columns:
  - operator, anchor, repeat;
  - gain;
  - `matrix_reads`, `cache_reads`, `positions`, `flips`;
  - `tour_hash`;
  - `wall_ns`: the untimed duration.

  `into_table` turns it into a Parquet table.
- **`application_stream(seed, label, op, anchor, repeat)`**: the random
  stream of one application. Because it is keyed by all five values, any
  single application can be replayed alone, in any order, in either stage.
- **`measure_gains(...)`**: for every operator, anchor and repeat:
  1. make the stream;
  2. mark, apply and record;
  3. compute the tour hash if the tour improved;
  4. undo.

  It checks loudly that no operator lengthened the tour and that every
  undo restored the length.
- **`struct GainJob` and its `Job::run`**: the stage-1 work for one state,
  dispatched to the right compiled copy (section 11.2).
  1. Build the tour and compute the flags.
  2. Choose the anchors:
     - from `anchors_from` if given;
     - all cities if there is no budget;
     - otherwise run a 6-anchor **pilot**, estimate the time per anchor,
       and compute how many anchors fit `min_rounds × 3 passes` in the
       budget (never fewer than `min_anchors`). Then draw that many with
       a seeded stream.
  3. Measure every gain.
  4. Estimate one timed round.
- **`InstanceCache`, `INSTANCES`, `instance_for`**: each worker thread
  keeps the last instance it loaded. Tasks come grouped by instance, so
  the same instance is not loaded again for each of its states. `Arc`
  shares the instance between the cache and the caller.
- **`meta_path(directory, kind, key)`**: `<kind>.meta/<key>.json`.
- **`run_gains(...)`**: runs `GainJob` on all states not yet done with
  `parallel_map`. For each, it writes the meta file and then the part
  file. The part file goes last, because its existence marks the state
  done.

#### Stage 2: times

- **`struct Expected`, `read_expected`**: stage 1's gain and tour hash
  for every (operator, anchor, repeat), read back from the part file, to
  check replays against. Rows of retired operators in old runs are
  skipped.
- **`struct TimingMeta`**: per state:
  - rounds timed and why timing stopped (`precision`, `budget` or
    `max_rounds`);
  - the core used;
  - frequency snapshots before and after, the average from the cycle
    counter, and the worker's reference frequency;
  - `suspect`: whether the frequency drifted by more than 1%;
  - the clock overhead and step.
- **`struct TimeRows`**: one row per per-call timed application.
- **`struct BatchRows`**: one row per (operator, round) of the batched
  passes:
  - batch time and restore batch time;
  - estimated restore time and the resulting time per application;
  - instruction and cycle counts.
- **`precise(values, precision)`**: is the 95% t-interval of the mean
  within `precision` (2%) of the mean? It uses the exact Student-t
  quantile from `statrs`. The comment records that before 2026-11-02 a
  small table was reused above 10 degrees of freedom, making the interval
  too wide.
- **`struct OperatorRound`**: what the three passes of one (operator,
  round) share: label, seed, operator, which repeat's streams to use, the
  shuffled anchors, their expected results, and the clock.
  - `mismatch` builds the error message for a replay that gained
    differently from stage 1.
  - **`per_call`**: one timed region per application. It checks each
    replay's gain and tour hash against stage 1, undoes, and records the
    time.
  - **`batch`**: one `sampled_batch` region around all anchors, then
    checks every delta against stage 1.
  - **`restores`**: record replays of the improving applications (it
    checks there are as many as stage 1 found), then time
    `timed_restores`.
- **`struct TimeJob` and its `Job::run`**: the stage-2 work for one
  state.
  1. Build the tour and spin the core up for 50 ms.
  2. For each round, shuffle the operator order. For each operator:
     - choose which repeat's streams to use (rounds cycle through the
       repeats of random operators);
     - shuffle the anchors and look up their expected results;
     - warm up;
     - run the passes in a random order;
     - turn the batch and restore samples into a time per application
       (`batched_time`) and counts (`batched_counts`);
     - append the rows, and record the round's mean.
  3. After `min_rounds`, stop when every deterministic operator's round
     means are `precise`, or the budget is spent, or `max_rounds` is
     reached.

  It returns the rows, the number of rounds, and the stop reason.
- **`run_times(...)`**: the timed stage.
  1. Check every state has stage-1 gains.
  2. Get the physical cores, quietest first, and check there are enough
     (one per timed worker plus one for the main thread).
  3. Check the machine is locked (`require_locked`).
  4. Pin the main thread to the last core and calibrate the clock.
  5. Write a `times-<timestamp>.json` record of the stage's conditions.
  6. Start one worker thread per timed core with `std::thread::scope`.
     Each worker:
     - pins itself;
     - opens its cycle counters (required unless `--allow-unlocked-clock`)
       and measures its reference frequency;
     - loops, claiming states with a shared atomic counter. For each, it
       runs `TimeJob`, measures the frequency before, during and after,
       marks the state `suspect` if it drifted, and writes the meta file,
       the batches part and the timings part (the last marks it done).

  An error in any worker is stored, and the other workers stop at their
  next state. The first error is returned.
  - **The closure trick.** The inner `(|| -> anyhow::Result<()> { ... })()`
    is a closure that is called immediately. It lets the worker use `?`
    inside a thread whose function cannot return errors.
  - **`.inspect_err(...)`** runs a side effect (a warning) on an error
    without changing it.

#### Stage 3: summarise

- **`struct RateSummary`**:
  - the rate, with its 2.5% and 97.5% bootstrap bounds;
  - the mean gain and mean time;
  - the fraction of improving applications, and the number of improving
    anchors.
- **`struct Grid`**: a table of values with one row per anchor and one
  column per repeat (gains) or round (times).
  - `build` checks every anchor has the same number of entries.
  - `row_means` gives each anchor's mean.
  - `stratified_means` is a bootstrap that keeps the anchors fixed and
    resamples within each anchor's row.
- **`summarise_operator(anchors, gains, times, sampled, resamples, rng)`**:
  the rate is (mean over anchors of the per-anchor mean gain) divided by
  (mean over anchors of the per-anchor mean time). The interval depends
  on how the anchors were chosen:
  - **sampled anchors:** a *cluster bootstrap*, resampling whole anchors
    (gain and time together), because the anchors themselves are a random
    sample;
  - **all cities:** a *stratified bootstrap*, resampling repeats and
    rounds within each anchor, because there is no sampling of anchors to
    account for.
- **`struct OperatorTime`**: an operator's times in one state:
  - `batched_ns`: the median over rounds of the batched time per
    application; this is the time used in the rate;
  - `per_call_ns`: the mean of the per-call times;
  - `restore_ns`.

  `scale()` is the factor that brings per-call times to the batched mean.
  Per-call times carry the spread over anchors (which the interval
  needs), but each includes clock overhead and noise; batched times give
  the right level. So per-call times are rescaled to the batched mean.
- **`operator_times_from`, `read_timing_parts`, `operator_times`**: read a
  state's batch and timing tables and build an `OperatorTime` per
  operator. The median over rounds means one interrupted batch cannot
  decide the state's time. `operator_times` is public because E1R fits
  its cost model on these times.
- **`struct CellRows`**: the big output table `cells.parquet`, with one
  row per (state, operator) and 36 columns: identity, rate and interval,
  times, class flags, timing diagnostics and layout. `into_table` names
  them.
- **`summarise_state(...)`**: for one state, reads everything stages 1
  and 2 wrote, and builds:
  - one `cells` row per operator, via `summarise_operator` with per-call
    times scaled;
  - a `nodes` table with per-anchor mean gain and time, for maps of
    where an operator gains.
- **`leaders_table(cells)`**: one row per state:
  - the leader and its rate, and the runner-up ("rival") and its rate;
  - `separated`: whether their intervals do not overlap;
  - `any_improvement`;
  - the leader **per application** (highest mean gain, ignoring time):
    Claim 1.5 compares it with the per-second leader;
  - `o7_optimal_o5_improves`: a state that is 2-opt optimal with
    neighbour lists but not with the full scan, which bears on Claim 1.6.
- **`operator_time_table(cells)`**: per (instance, operator), the batched
  and per-call means averaged over states, plus logged quantiles of their
  ratio. It is a check on the two timing methods, not an input to rates.
- **`run_summarise(...)`**: checks every state was timed, summarises all
  states in parallel, writes `nodes` parts, merges every state's cell rows
  and writes `leaders.parquet`, `operator_times.parquet` and
  `cells.parquet`.

  The small `macro_rules! extend` defines a local macro that writes
  `cells.instance.extend(part.instance);` and so on for each listed
  field. It saves 36 nearly identical lines.

#### Entry points

- **`enum Stage`**: `Gains`, `Times`, `Summarise`, `All`.
- **`run(...)`**:
  1. If `config.json` exists, check the settings are identical (a loud
     error if not). Otherwise write it.
  2. Select the tasks.
  3. Run the requested stages. For summarise, it first loads each
     instance's optimum, to compute gaps.
- **`type RateMap` and `read_rates(cells)`**: rates as a map from
  (instance, seed, state) to (operator → rate). The population runs'
  greedy and softmax arms read it.
- **`read_bool`**: re-export of `column_bool`.

### 11.6 `experiments/e0.rs`: can the clock be trusted? (`aos e0`)

**What.** Before any operator rate is believed, E0-rs measures whether
the timing is precise and repeatable, and what the basic things cost. It
runs on uniform instances generated in-process (100 to 5,000 cities) and
answers seven questions. The file numbers them as in the design note,
and runs them in the order 1, 4, 5, 2, 7, 6, 3:

| # | Question | Main output |
|---|---|---|
| 1 | Is the clock stable? Calibration at start and end, overhead and resolution | `summary.json` clock section |
| 4 | What does one distance read cost, against n? | `reads.parquet` |
| 5 | What does one flip cost, on each layout, against n? | `flips.parquet` |
| 2 | What does one application of each operator cost, and is that repeatable? (**the repeatability gate**) | `costs.parquet`, `cost_batches.parquet`, `repeat_cells.parquet` |
| 7 | How much do per-call clock reads add, compared with batches? | `per_call.parquet` |
| 6 | How long is a full 2-opt descent? | `descents.parquet` |
| 3 | Do several timed workers slow each other down? (**the contention gate**) | `contention.parquet`, `contention_cells.parquet` |

**The gates**, the pass/fail rules:
- **`GATE_CELL_CV = 0.05`:** for each cell (n, layout, state, operator),
  the coefficient of variation of the batched time between repeats must
  be at most 5%, judged by its 95% interval. Until 2026-11-02 the gate
  looked only at the median and 95th percentile over all cells, which let
  the steady cells hide the unsteady ones.
- **`GATE_SLOWDOWN = 0.02`:** with k workers running at once, no
  operator's cell may be more than 2% slower than with one worker.
- **`FREQUENCY_DRIFT = 0.01`:** a timed block whose core frequency moved
  by more than 1% is flagged as suspect. Measurement A uses this constant
  too.

The 2026-11-02 journal entry records the outcome: the gate was **not
passed**, with 111 cells unresolved.

#### Settings and setup

- **`struct Settings`**: seed; sizes for each question; anchors, repeats
  and rounds of the cost sweep; the contention sizes, worker counts and
  phase lengths; read, flip and descent counts.
  - `full()` is the real run; `quick()` is a few seconds that exercises
    every step.
  - `state_names(n)` gives `random`, `opt2` and `lkopt` up to 1,000
    cities, and only the last two above.
- **`struct StateSet`**: one generated instance plus its states.
  `problem()` gives the main-design `Problem`.
- **`build_states(settings, n)`**: generate the instance with alpha lists,
  then build three states:
  - a random tour;
  - its full 2-opt descent;
  - an LK optimum, from repeated O18 passes until none improves.
- **`on_core(cpu, job)`**: run a closure on a new thread pinned to `cpu`
  and return its result.
- **`MachineLog`**: a table of machine-state snapshots (frequency,
  governor, boost, temperature, fabric and memory clocks), recorded at
  every block. It goes to `machine.parquet`.
- **`cool_down(opening)`**: before the closing single-worker phase of the
  contention test, wait, idle, until the CPU is back within 3 °C of its
  opening temperature (at most 3 minutes). Otherwise the closing phase
  measures the heat left behind by the many-worker phases, not drift.
- **`khz(cpu)`, `drifted(before, after)`**: frequency snapshot, and a
  more-than-1%-change test.

#### 1. Clock

- **`struct ClockReading`, `read_clock(stage)`**: calibrate (10 windows
  of 100 ms), then report:
  - quantiles of 100,000 empty regions;
  - the smallest non-zero step between consecutive reads.

  It is done at the start and at the end. The summary reports how much
  the TSC rate drifted in between.

#### 4. Distance reads

**How to time a single memory read.** Many reads are timed in a loop and
the loop's own cost is subtracted.
- **`xorshift(x)`**: a tiny random generator, cheap enough not to
  dominate the loop, and unpredictable enough that the CPU's prefetcher
  cannot guess the next address.
- **`city_below(x, n)`, `city_pair(x, n)`**: turn random bits into city
  indices with a multiply-shift instead of a slow `%`.
- **`trait Reader`**: "do one kind of read, given random bits". The
  implementations are:
  - `DistReader`: a distance from any storage;
  - `CacheReader`: a candidate-list distance;
  - `PairBaseline`, `SlotBaseline`: do the same index arithmetic without
    touching memory.

  A read's own cost is its time minus its baseline's time.
- **`read_loop<R, CHAIN>(reader, reads, seed)`**: the timed loop. With
  `CHAIN = true`, each read's address depends on the previous value read.
  That measures **latency**, because the CPU must wait. Without it, the
  reads are independent, which measures **throughput**, because the CPU
  overlaps them. Operators sit between the two.
- **`reads_rows(matrix, reads, seed)`**: reads whole rows sequentially,
  the fast case.
- **`measure_reads(...)`**: for each size, builds the storages (matrix,
  computed `EUC_2D`, computed `GEO` with made-up coordinates, and the
  candidate cache) and times each mode and access pattern, with repeats.
  - `type Measure` is a (name, baseline name, boxed closure) triple.
  - `Box<dyn Fn...>` lets one list hold closures of different types.
- **`read_baseline(mode)`**: which baseline belongs to which mode.

#### 5. Flips

- **`flip_block<L>(settings, clock, n, short, repeat, rows)`**: times a
  long sequence of reversals on one layout as one region.
  1. **Draw the flips** on a copy of the layout (`draft`). This is needed
     because a "short" flip's end point depends on the tour at that
     moment.
  2. **Replay** the same flips, timed, on the real layout.
  3. **Check** both ended identical.

  Random flips have any length; short flips have length up to
  `short_flip`. It records ns per flip and storage positions per flip.
- **`measure_flips(...)`**: every size, both layouts, both flip kinds,
  with repeats.

#### 2. Cost per application (the repeatability gate)

- **`CostRows`** (per-call rows) and **`CostBatchRows`** (one row per
  (n, layout, state, operator, repeat, round): batch time, restore,
  time per application, counts, how many samples were retaken, and
  whether the block was suspect).
- **`struct Frequency`**: the timed thread's cycle counters and its
  reference frequency.
  - `on_this_thread()` opens them.
  - `drifted_since(before)` checks a block against the reference.
- **`cost_block<L>(...)`**: times every operator on one state exactly as
  measurement A does: operators in turn, each over all anchors back to
  back, in the per-call, batch and restore passes, with several rounds
  per repeat.
  - **The key design point** (from its comments): the operator order,
    pass order, anchor order and random streams are **the same in every
    repeat**. So repeats replay *identical* work, and any spread between
    repeats is the machine's, not the experiment's.
  - **Why this was changed.** Before 2026-11-02 the order was drawn per
    repeat, and the order alone moved cheap 2-opt operators' batched
    times by up to 2.5×, through which distance rows happened to be in
    cache.
  - **Checks.** Every application must reproduce its expected change.
    Per-call rows are kept for the first round only, as a diagnostic.
- **`label_key(label)`**: a hash of a label, for stream keys.
- **`cost_anchors(settings, n, state)`**: a fixed seeded anchor sample
  per state.
- **`type Changes`, `expected_changes(...)`**: every (operator, anchor)
  length change, computed untimed first. Each timed pass must reproduce
  it, and it tells which operators need a restore pass.
- **`measure_costs(...)`**: computes the expected changes for all states.
  Then, for each repeat and size, it runs `cost_block` on both layouts
  for each state. Repeats are the *outer* loop, so they are spread over
  the whole run instead of being taken back to back; that makes the gate
  test stability over time.

#### 6. Descents

- **`descent<L>(...)`**: one full O7 descent from a random tour, timed as
  one region, with the tour checked afterwards.
- **`measure_descents(...)`**: every size and layout, several repeats.

#### 3. Contention (the contention gate)

**The question.** Measurement A can time on several cores at once only if
the workers do not slow each other down, through the shared L3 cache,
memory bandwidth or power limits.

- **`ContentionRows`, `Totals`**: per worker, round, size, state and
  operator: applications, total time, counts and frequency.
  `append(other)` merges two workers' rows.
- **`contention_worker(...)`**: one worker of one phase. It repeats
  identical rounds until a deadline. Each round times every operator on
  every (size, state) the way measurement A does (warm-up, then
  `sampled_batch`), in an order drawn per round but the same for every
  worker and phase. A round still running at the deadline is discarded.
  A `Barrier` makes all workers of a phase start together.
- **`measure_contention(...)`**: the phases. The default is 1, 2, 4, 6, 7,
  then 1 worker again. The opening and closing single-worker phases are
  longer.
  - **The phases.** Each phase spawns that many pinned workers on the
    quietest cores.
  - **Before the last phase,** `cool_down` waits for the temperature to
    settle.
  - **Failure rule.** A worker that completes no round is an error: the
    phase was too short.

#### 7. Per-call against batched timing

- **`TIMING_MODES`**: the three ways of timing the same work:
  - `mixed_per_call`: one region per call, operators interleaved anchor
    by anchor (the old measurement A);
  - `grouped_per_call`: one region per call, grouped by operator;
  - `batched`: one region per operator.
- **`unchanged_anchors(settings, set)`**: anchors of the LK-optimal state
  where *no* deterministic operator improves. There, every mode does
  exactly the same work and no restore is needed, so the modes can be
  compared fairly.
- **`per_call_block<L>(...)`**: one repeat on one layout. Every
  deterministic operator at those anchors, timed in each mode, in a
  shuffled mode order.
- **`measure_per_call(...)`**: all sizes, layouts and repeats.
- **`per_call_summary(rows)`**: per (n, layout, operator), the medians of
  each mode and the ratios of the per-call modes to the batched one. That
  is how much the clock reads themselves inflate a per-call time.

#### Summaries and gates

- **`type CellKey`**: (n, layout, state, operator).
- **`enum Verdict`**: a gate's verdict on one cell, judged by its 95%
  interval:
  - pass: wholly inside the limit;
  - fail: wholly beyond it;
  - unresolved: it straddles the limit, so there are too few samples to
    tell, and the gate fails until more samples resolve it.

  `at_most(low, high, limit)` is for quantities that must not exceed a
  limit (CV, slowdown); `within(low, high, limit)` is for changes that
  must stay within ±limit (drift).
- **`struct RepeatCell`**: one repeatability cell: mean time, CV, its
  interval and verdict.
- **`cell_cvs(...)`**: the CV between repeats of each cell, sorted from
  largest.
- **`repeatability(batches, rows)`**: the repeatability gate. It is
  judged on the batched time. It also reports, as diagnostics, the same
  CVs for per-call means, instructions and cycles:
  - instruction counts of identical work should not vary at all;
  - cycles that vary as much as time mean the spread is in the core's
    own execution, not time lost to other tasks.
- **`CONTENTION_QUANTILE = 0.25`**: a contention cell's level is the 25th
  percentile over (worker, round). Disturbances only ever slow a round,
  so a low quantile ignores them without trusting the single fastest
  round.
- **`phase_changes(...)`**: per phase and operator, the change against the
  first phase, as a summary.
- **`ContentionCell`, `contention_cells(settings, rows)`**: every (operator,
  n, state) cell of every later phase against the opening phase, with a
  bootstrap interval of the change (1,000 resamples).
  - **Many-worker phases** pass a cell if the whole interval is at most
    +2%. A speed-up is no harm: workers share distance rows in L3.
  - **The closing single-worker phase** must be within ±2%, because it
    tests drift of the machine itself.
- **`contention_gate(...)`**: a phase passes if every cell passes. The
  recommended worker count is the largest count such that it and every
  smaller tested count pass. Measurement A's `--timed-workers` comes from
  this.
- **`counted_phases(...)`**: the same slowdowns for instructions and
  cycles, as diagnostics.
- **`crossover(rows, flips)`**: the representation threshold. Per n, the
  geometric mean over (state, operator) of two-level time / array time.
  The proposed `n_split` is the smallest n from which the two-level list
  is faster at every larger measured n. This is where `--n-split 1000`
  comes from.
- **`read_summary`, `cost_table`, `descent_summary`, `per_call_markdown`,
  `cell_list`, `repeat_non_passing`**: summary tables and Markdown pieces.
- **`regate(directory)`** (`aos e0-gates`): recomputes all three gates of
  a finished run from its tables with the *current* rules, writing to
  `<run>/gates/`. Runs made under earlier rules can then be judged by
  today's, without rerunning hours of timing.
- **`repeat_cells_table`, `contention_cells_table`, `markdown`**: output
  formatting.

#### `run(repository, settings, allow_unlocked, out)`

1. Check there are enough physical cores (the largest worker count plus
   one for the main thread). Pick the quietest core for timing and the
   last for the main thread.
2. Check the machine is locked (or note the problems and put
   `-unlocked` in the folder name).
3. **Untimed:** build every size's instance and states in parallel, one
   thread per size.
4. Pin the main thread, record the machine state, read the clock on the
   timed core, and write `config.json` (settings, cores, clock, instance
   digests and state lengths).
5. **Timed, on the timed core:** reads, flips, costs, per-call check,
   descents.
6. **Contention phases** on several cores.
7. Read the clock again.
8. Compute the gates and summaries, and write every table plus
   `summary.json` and `summary.md`.

### 11.7 `experiments/population.rs`: population runs (`aos population`)

**What.** A small evolutionary search in which every step gives one
tour one "slice" of one operator. The **arm** decides which operator:
fixed, uniform at random, an adaptive operator-selection rule, or rules
that read measurement A's rate table. This is the machinery for
experiments E2 and E3 (does choosing operators by state beat fixed
choices, at equal time?) and the greedy-vs-softmax pilot. So far only its
count-mode replay check has run in Rust (the 2026-11-02 night); E2 and
E3 themselves have not (roadmap P5).

**One step of the search (steady-state).**
1. Pick a tour by **tournament**: draw `tournament` tours at random and
   take the shortest.
2. Copy it.
3. Ask the arm for an operator. The level arms first **classify** the
   copy (find its local-optimality class); that time is recorded but not
   charged to the budget, since it is reference knowledge an online
   selector would not have.
4. Give the copy one slice of that operator.
5. If the copy is shorter than the population's worst tour, *and no tour
   already has that exact length* (to keep diversity), it replaces the
   worst.

**Four separate random streams** (initial tours, selection, arm,
operators) come from the seed. So two arms with the same seed start from
the same population and draw the same tournaments, and their difference
is only the arm.

**Slices.** A slice ends either at a wall-clock deadline (time mode) or
after a fixed number of applications (count mode). A time-mode run logs
how many applications each slice made, so it can be **replayed exactly
in count mode**. `--check-replay` does this and stops if the replay
reaches a different best tour. That is the reproducibility check for
timed runs, whose exact slice contents depend on the machine's speed.

**The types.**
- **`FLOOR_SHARE = 0.2`**: the total probability the adaptive arms always
  keep spread over all operators, so none is starved.
- **`enum ArmSpec`**: the arm as typed on the command line:
  - `Fixed(name)`;
  - `Uniform`;
  - `ProbabilityMatching` (`pm`);
  - `AdaptivePursuit` (`ap`);
  - `Greedy`;
  - `Softmax(tau)`.

  Its methods:
  - `parse(text)` reads `fixed:<op>`, `uniform`, `pm`, `ap`, `greedy` or
    `softmax:<tau>`, and refuses retired operators and non-positive
    temperatures.
  - `label()` is the name used in files.
  - `needs_class()` is true for the two arms that read the rate table.
- **`struct SliceRecord`**: what one slice did:
  - its duration, and the overshoot past the deadline;
  - applications, improving ones, gain;
  - work, and the last improving anchor.
- **`enum Arm`**: the arm's internal state while running. Two variants
  learn as they go:
  - `Matching`: quality estimates and probabilities;
  - `Pursuit`: the same, plus a maximum probability.

  `Greedy` and `Softmax` hold the rate table. `Replay` holds a recorded
  list of (operator, count) and a position in it.
- **`draw(probabilities, rng)`**: picks an operator with the given
  weights, using `rand`'s `WeightedIndex`. The comment records a bug it
  replaced: a hand-written version could return an operator with weight
  zero because of rounding.
- **`Arm::new(spec, table)`**: the starting state. Learning arms start
  uniform, with floor `0.2 / 16` per operator.
- **`Arm::next(rng, class)`**: the operator for the next slice.
  - `Greedy` takes the highest rate in the tour's class.
  - `Softmax` gives each operator weight `(rate / top rate)^(1/tau)`.
    That is a softmax on log rates: small `tau` is nearly greedy, large
    `tau` nearly uniform.
  - `Replay` returns the next logged entry with its count.
- **`Arm::update(op, record)`**: learning after a slice. The reward is the
  slice's gain per second.
  - *Probability matching:* move the chosen operator's quality 10% towards
    the reward; set each probability to floor + share × quality / total.
  - *Adaptive pursuit* (Thierens 2005): the same quality update, then move
    every probability 10% towards a target. The target is the maximum
    for the current best operator and the floor for the others.
- **`struct Config`**: population size, tournament size, slice length
  (seconds *or* applications), and the budget (seconds, applications
  and/or slices; whichever comes first). `validate()` checks:
  - exactly one slice kind is set;
  - at least one budget is set;
  - the tournament size is in a sensible range.
- **`struct SliceLog`**: one entry per slice:
  - start, duration, overshoot;
  - operator, and the previous operator;
  - applications, improving, gain;
  - length before and after, and whether the copy was accepted;
  - lookups, moved, anchor;
  - the population's best length;
  - the class.
- **`struct RunResult`**: the best length and its tour hash, charged and
  uncharged time, and the log.
- **`DEADLINE_CHECK_NS = 1000`**: about how often (in ns) a time-mode
  slice looks at the clock.

**The functions.**
- **`run_slice(op, tour, problem, rng, clock, seconds, count)`**: one
  slice.
  - **Count mode:** apply `count` times.
  - **Time mode:** apply until the TSC passes the deadline. The
    application running at the deadline is finished and its overshoot
    recorded.

    **How often to check the clock.** Reading the clock costs about as
    much as a cheap operator application, and that cost would be charged
    to the operator. So the slice estimates its pace (ns per application
    so far) and checks the clock only about every microsecond (and never
    later than the time left).
  - `clear_log()` after every application, because a slice's changes are
    permanent.
- **`struct PopulationJob` and its `Job::run`**: the search itself.
  1. Build the random initial population.
  2. Make the four streams.
  3. Loop until a budget is spent: tournament, copy, optional classify
     (timed, then subtracted from the charged time), `arm.next`,
     `run_slice`, `arm.update`, replace the worst if better and new, and
     log the slice.

  At the end it checks the best tour and returns the result.
  `elapsed_ns` is a closure: the time since the start minus the
  uncharged time.
- **`run_population(...)`**: validate the config, build the arm, and
  `dispatch` the job.
- **`replay(...)`**: re-run a logged run in count mode, with a `Replay`
  arm that feeds back the logged operators and counts.
- **`rate_table(cells, instance)`**: the table the level arms read. For
  each of the 4 classes and each of the 16 operators, it takes the median
  rate over this instance's states of that class. If the instance has no
  state of a class, it uses the median over all instances. Retired
  operators' rows in old runs are skipped.
- **`struct Settings`**: instances, arms, seeds, config, rate table
  path, implementation and `check_replay`.
- **`run(repository, directory, settings, workers, allow_unlocked)`**:
  1. Validate, check the cores and the machine lock, pin the main thread,
     calibrate, and write `config.json`.
  2. List every (instance, arm, seed) job.
  3. Start one pinned worker per timed core. Each worker claims jobs with
     an atomic counter and keeps the last instance loaded. It skips jobs
     whose part already exists (resume), loads the rate table once per
     instance (shared through a `Mutex<HashMap>`), runs, optionally
     replays and compares, and writes the slices and run parts.
  4. Merge the `runs` parts and write a Markdown summary.
- **`write_slices(...)`**: writes one run's per-slice log
  (`slices.parts/<key>.parquet`) and its one-row summary
  (`runs.parts/<key>.parquet`).

### 11.8 `experiments/rollouts.rs`: E1R, Claim 1.4 (`aos e1r`)

**The question.** Measurement A finds the operator with the best rate
for the *next* application from a state: the greedy choice G(s). Is that
also the best *first move* when you look further ahead? E1R answers this
by simulation.
1. **Choose candidates.** For a sample of measured states, take several
   candidate first operators (greedy first).
2. **Roll out.** For each candidate, run `repeats` rollouts: one slice of
   the candidate, then a fixed continuation policy, "greedy by level"
   (always use the measured leader of the tour's current class), up to a
   horizon.
3. **Compare.** At each horizon, test whether some alternative ends with
   a shorter tour than greedy, by more than chance.

**Paired rollouts.** All candidates of a state share the random streams
of each repeat: repeat 7 of every candidate uses the same continuation
stream. So the comparison between candidates is paired (common random
numbers), which removes much of the noise. Because the streams do not
depend on which candidates exist, dropping candidates afterwards
(`--max-candidates`) gives exactly the analysis of a smaller plan.

**The virtual clock: why rollouts are not timed.** Timing tens of
thousands of rollouts on one quiet core would take weeks. Instead, every
application is *charged* a predicted time:

    cost = a + b × lookups + c × moved   (ns)

with `a`, `b` and `c` fitted per instance and operator. Rollouts then run
untimed on every CPU, and a slice ends when its *virtual* time is used
up. A rollout becomes a pure function of its seeds: it gives the same
result on any machine and any CPU. Two stages keep the virtual clock
honest:
- **`calibrate`** times real rollouts on one pinned core and fits the
  coefficients on them.
- **`validate`** replays a sample of rollouts with real timing and
  compares real with virtual time. The ratio is the "real/virtual" number
  in the journal (R1 1.002; R2's 1.25 for 3-opt is the reason R2 needs
  its own calibration).

**The stall rule (decision D1).** "Greedy by level" can get stuck. A tour
that only the full 2-opt scan can improve is in class "2-opt open", but
that class's leader may be unable to improve it. With `--stall-rule skip`
(the decided setting), after a slice that gains nothing the policy moves
to the next operator in that class's ranking, until the tour improves.

**The five stages.**

| Stage | Where it runs | Output |
|---|---|---|
| `plan` | untimed | `plan.json` (states, candidates, policy rankings), provisional `cost_models_e1.json`, `cost_check.parquet` |
| `calibrate` | one pinned core, timed (or copied with `--cost-models-from`) | `cost_models.json`, `calibration.parquet`, `calibrate.json` |
| `run` | all CPUs, untimed | `events.parts/`, `slices.parts/`, `repeats.parts/` |
| `validate` | one pinned core, timed | `validate.parquet` |
| `analyse` | untimed | `arms`, `states`, `summary` `.parquet`, `summary.md`, and the same under `by-time/` |

#### Constants and settings

- **`SIZES = [100, 200, 500]`**: size groups of the E1 instance set.
- **`BOOTSTRAP = 2000`**: resamples for intervals.
- **`FLIPS = 10_000`**: sign flips per permutation test.
- **`MIN_REPEATS = 8`**: fewest repeats to test on.
- **`ALPHA = 0.05`**: the family-wise error rate per state and horizon.
- **`enum StallRule`**: `None` or `Skip`.
- **`struct Settings`**: fixed at `plan` and stored in `config.json`:
  - the measurement run;
  - instances per (family, size) cell, states per class;
  - candidates and repeats;
  - slice length per city (ns), horizon in slices;
  - the seeds whose timings fit the provisional clock, and the held-out
    check seed;
  - the run seed;
  - validation rollouts and calibration horizon;
  - the stall rule.

#### The cost model

- **`struct Coefficients`**: `intercept` (a), `per_lookup` (b),
  `per_moved` (c).
  - `cost_ns(work)` gives the predicted ns of one application.
- **`struct Sums`**: the running sums a least-squares fit needs.
  - **How the fit works.** It stores the Gram matrix X'X and the moment
    X't for x = (applications, lookups, moved). These can be added up as
    data arrives, and the fit needs nothing else.
  - **One fit for both kinds of sample.** A sample can be one application
    or a whole slice of many (`add_group`). The model `t = a·n + b·l +
    c·m` fits both, with `a` the cost per application.
  - `add`, `add_group` and `merge` accumulate; `predicted_ns` evaluates
    coefficients on the totals.
- **`Sums::fit()`**: least squares with every coefficient non-negative
  and a positive intercept.
  1. Try the four feature subsets that include the intercept (a; a+b;
     a+c; a+b+c).
  2. Solve each with an SVD (`least_squares`), which handles features
     that are exact multiples of each other: a double bridge reads the
     same number of distances every time.
  3. Keep the best fit with no negative coefficient.

  The long comment records why the intercept is forced. On 2026-11-03 a
  fit without it put all of an operator's cost on "moved"; an application
  that moved nothing then cost 0 ns, and the virtual clock stopped.
- **`least_squares(gram, moment, active)`**: the SVD solve, restricted to
  the active features.
- **`type CostModel`**: operator name → coefficients, per instance.
  **`coefficients(model, op)`** looks one up, and panics if missing (the
  plan checks that every needed operator has one).

#### The policy

- **`struct Policy`**:
  - per class, a ranking of operators (leader first);
  - the stall rule;
  - the cached tour length and class;
  - the skip count.
- **`Policy::choose(tour, problem, last_gain)`**: the operator for the
  next slice.
  - **Reclassify only on change.** If the tour's length changed, classify
    it again and reset the skip count. Operators only shorten tours, so
    an unchanged length means an unchanged tour, and classifying (which
    is expensive) is skipped.
  - **Stall.** If the length did not change and the stall rule is `Skip`,
    move one step down the ranking.

  It returns the operator and the class.

#### One rollout

- **`struct RolloutResult`**:
  - the virtual time and tour length after every improving application
    ("events");
  - per slice: start time, operator, class and application count;
  - the end time, final length, and whether the optimum was reached;
  - in timed mode only, the real time and the work and time sums per
    operator, for calibration.
- **`struct Rollout` and its `Job::run`**: the rollout itself.
  1. Build the tour.
  2. Loop over slices. For each slice, keep applying the operator. Each
     application is charged `cost_ns(work)`, which must be positive or
     the run stops. The slice ends once the virtual clock passes its end.
     Improving applications are logged as events.
  3. **Timed mode** (calibrate and validate) reads the real clock only
     around whole slices, so the applications run back to back exactly
     as in untimed mode. The fit then regresses slice time on the slice's
     totals.
  4. **Stop** when the optimum is reached or the horizon is passed.
     Otherwise ask the policy for the next operator.

  **Two streams.** The first slice uses its own stream, and every later
  slice uses the continuation stream. Neither stream depends on which
  candidate is being rolled out.
- **`length_at(initial, event_ns, event_length, at_ns)`**: the tour length
  at any virtual time: the length after the last event at or before it.
  Binary search with `partition_point`.

#### Plan

- **`struct PlannedState`**:
  - instance, path, family, n, seed, state name;
  - its class;
  - the greedy operator;
  - the candidates and their measured rates;
  - the tour.
- **`struct Plan`**: the chosen instances, the policy rankings per
  instance and class, the states, and the measurement's implementation.
- **`nested_draw(rng, population, count)`**: shuffle the whole
  population and keep the first `count`. The stream always uses the same
  number of random draws whatever `count` is, so a plan with 3 states per
  class picks exactly the first 3 of the 6 that a plan with 6 would pick.
  This is why R3's states are a subset of R1's.
- **`size_group(n)`**: the nearest of 100, 200 and 500.
- **`LeaderRow`, `read_leaders(path)`**: rows of measurement A's
  `leaders.parquet`.
- **`without_retired(leaders, rates)`**: adapts a measurement made before
  the 2026-11-05 retirement. It drops retired operators' rates, and gives
  each state led by a retired operator the best current operator as its
  leader, logging every relabel.
- **`type Scope`, `rankings(leaders, instance)`**: the policy's ranking
  per class. Operators are ranked by **how many improving states of that
  class they lead**. The counts come from the narrowest scope that has
  any such state: the instance itself, then its (family, size) cell, its
  family, and finally everything. Ties go to the lower operator number.
  The scopes are a list of boxed closures, each a different filter.
- **`timing_sums(measurement, instance, seeds, only)`**: builds cost-model
  sums from measurement A's timings. Each per-call time is joined with
  its application's counted work from the gains table (by operator,
  anchor and repeat) and scaled to that state's batched mean.
- **`plan(repository, directory, settings)`**:
  1. Refuse to overwrite an existing plan.
  2. Read the measurement's settings, leaders and rates, and drop retired
     operators.
  3. **Pick instances:** group by (family, size) and `nested_draw`
     `instances_per_cell` from each.
  4. **Pick states:** for each instance and class, the pool is the
     improving states of that class, sorted by seed and state order;
     `nested_draw` `states_per_class` from it.
  5. **Pick candidates** for each state, in this order:
     - greedy;
     - the policy's own choice for that class, if different and
       improving;
     - then the best remaining operators by rate, as long as their rate
       is positive, up to `candidates`.

     It checks that the leaders table and the rates agree on greedy.
  6. **Provisional clock:** fit a cost model per instance and operator on
     the fit seeds, and check it on the held-out seed (relative error per
     state and operator, in `cost_check.parquet`).
  7. Write the plan, the models and `config.json`, and log the run size.
- **`planned_op(name)`**: an operator from a plan, with a clear error if a
  plan names a retired operator.

#### Run directory helpers

- **`struct Task`**: one (state, candidate) pair. **`tasks_of(plan)`**
  lists them, largest instances first.
- **`read_json`, `load_settings(directory)`**: read files of the run.
- **`label(state)`, `task_key(state, candidate)`**: identifiers for
  streams and file names.
- **`ranking_ops(plan, instance)`**: the rankings as `Op` values.
- **`run_task(instance, plan, settings, model, task, repeats, clock,
  calibration)`**: runs a range of repeats of one task.
  - **Streams:** keyed by the state label and the repeat
    (`"e1r:first"`, `"e1r:continue"`), not by the candidate. That is the
    pairing.
  - **Calibration rollouts** use different streams, keyed by candidate,
    and a shorter horizon.

#### Calibrate, run, validate

- **`calibrate(repository, directory, allow_unlocked)`**:
  1. Check the lock, pin to the quietest core, calibrate the TSC.
  2. Run one timed calibration rollout per task, collecting work and
     time sums per instance and operator. The `collect` closure keeps one
     instance loaded at a time.
  3. If an operator the instance needs (a candidate or anywhere in a
     ranking) never ran, time it directly with one-slice rollouts.
  4. Fit every model and record how the provisional fit compares with
     the measured time (`calibration.parquet`).
  5. Write `cost_models.json`.
- **`reuse_calibration(directory, source)`**: the shared calibration of
  the 2026-11-05 night. It:
  - checks both runs measured the same implementation;
  - checks the source has a model for every instance and operator this
    run needs;
  - copies `cost_models.json` **byte for byte**;
  - records the source and the file's SHA-256 in `calibrate.json`.

  Why copy bytes: parsing and writing the JSON again could change a
  coefficient in its last digit, and then the two runs would not share
  exactly one clock.
- **`run(repository, directory, workers)`**: every task without a
  `repeats` part, on all CPUs (`parallel_map`). Each task runs all its
  repeats and writes its results. It logs progress with an estimate of
  the time left.
- **`write_results(...)`**: three parts per task:
  - `events` (repeat, time, length);
  - `slices` (repeat, start, operator, class, applications);
  - `repeats` (end time, final length, reached optimum, initial length,
    slice length). It is written last, because it marks the task done.
- **`validate(repository, directory, rollouts, allow_unlocked)`**:
  requires a finished run.
  1. Pin and calibrate.
  2. Draw a sample of (task, repeat) pairs, and replay each with real
     timing.
  3. **Each replay must reproduce the stored final length and end time
     exactly**, or the stage fails. This proves the run is a pure
     function of its seeds.
  4. Record real against virtual time, in total and per operator.

#### Analyse

- **`horizons(horizon_slices)`**: 1, 2, 4, … up to the horizon.
- **`type GapsAndTimes`, `gaps(directory, key, optimum, cuts)`**: for
  each repeat, the gap to the optimum at each cut, and the virtual time
  at which that gap was reached (its last improvement up to the cut). It
  checks events are in time order.
- **`sign_flip_p(differences, rng)`**: a one-sided paired permutation
  test of "greedy's gap minus the alternative's is above zero on
  average". Under the null, each paired difference is equally likely to
  have either sign, so the observed mean is compared with the means of
  10,000 random sign flips. The p-value is (1 + count ≥ observed) /
  (10,001). It returns 1 immediately if the observed mean is not
  positive.
- **`best_candidate(candidates, mean_gap, mean_reached_ns)`**: the
  candidate with the lowest mean gap.
  - Ties go to the candidate that reached its gap soonest (the faster
    operator under a time budget).
  - Exact ties on both go to the earliest in plan order.

  It also returns how many tied on the gap (`tied_best`).
- **`holm_rejections(p_values, alpha)`**: Holm's step-down procedure.
  Sort the p-values. Compare the k-th smallest with `alpha / (m − k)`, and
  stop at the first that fails. Everything before it is rejected. Its
  first step is exactly Bonferroni, so "greedy is beaten" is decided as
  Bonferroni would, but more alternatives can be rejected.
- **`holm_adjusted(p_values)`**: the matching adjusted p-values (the
  `holm_p` column): `(m − j)·p` with a running maximum, capped at 1.
- **`paired_interval(differences, level, rng)`**: a bootstrap interval
  for the mean difference. The challenger's interval uses the Bonferroni
  level `1 − 2α/m`, which is conservative for any Holm rejection.
- **`half_mean(values, parity)`**: the mean over even or odd repeats.
  **`lowest_on_half(...)`**: the candidate with the lowest score on one
  half.
- **`crossfit_regret(by, candidates, greedy)`**: regret without the
  winner's curse. Choose the best candidate on even repeats and measure
  greedy minus it on odd repeats, then the reverse, and average.
- **`slice_rate_advantage(first_slice, at_cut, candidates, greedy)`**:
  question 3. On one half, pick the candidate with the lowest gap after
  its first slice (the most gain over a whole slice), then measure its
  advantage over greedy at the cut on the other half. Both ways,
  averaged.
- **`catch_up(labels, greedy, best)`**: for each cut, the first later cut
  where greedy's mean gap is no worse than the best candidate's was at
  the earlier cut. That is how long greedy needs to catch up.
- **`analyse(repository, directory, max_candidates)`**: loads every
  instance's optimum and writes two analyses:
  - one on the **slice grid** (cuts at 1, 2, 4, … slices);
  - one on the **time grid**, in `by-time/` (cuts at 10, 20, 40, … µs per
    city).

  With `--max-candidates k`, it analyses only the first k candidates and
  writes to `analysis-top<k>/`.
- **`TIME_GRID_BASE_US`, `time_grid(window)`**: the time grid up to the
  run's window.
- **`enum CutGrid`**: `Slices` or `MicrosPerCity`, with these methods:
  - `labels`: the cut labels;
  - `cut_ns`: the cut times for one state;
  - `column`, `heading`: the table column name and the Markdown heading;
  - `stream`: the analysis stream's label, so the two grids draw
    independent random numbers.

  **Why two grids.** A slice of R2 is a tenth of a slice of R1. Comparing
  runs at "the same number of slices" compares different amounts of
  time; the time grid compares equal elapsed time.
- **`struct AnalysisRun` and `write(output, grid)`**: the analysis
  itself. For each state:
  1. Take the first k candidates (greedy must be first), and read every
     candidate's gaps and reach times at every cut.
  2. Require at least 8 repeats.
  3. At each cut:
     - compute paired differences greedy − alternative, and a
       sign-flip p-value for each alternative (all of them before any
       decision, so the random stream does not depend on the outcome);
     - apply Holm. If anything is rejected, the challenger is the
       rejected alternative with the largest mean advantage, and it gets
       a paired bootstrap interval at the Bonferroni level;
     - record one `arms` row per candidate: mean gap, standard
       deviation, measured rate, Holm-adjusted p;
     - record one `states` row: best candidate and ties, regret,
       cross-fitted regret, slice-rate advantage, beaten or not, smallest
       p, the challenger's advantage and interval.
  4. Compute the catch-up column from the per-cut means.

  It then groups the state rows by class, by family and overall, per
  cut: the share where greedy is beaten, the share where greedy is best,
  and median regrets. It writes `arms.parquet`, `states.parquet`,
  `summary.parquet` and `summary.md`. Missing values (no challenger) are
  stored as nulls, using the `(!v.is_nan()).then_some(v)` conversion.

#### Tests

These check:
- ties go to the faster candidate, then to plan order;
- the time grid covers each window and matches R1's cuts;
- catch-up finds the right cut;
- the slice-rate selector is cross-fitted;
- Holm rejects at least what Bonferroni rejects, stops at its first
  failure, and its adjusted p-values are monotone and consistent with its
  rejections;
- smaller nested draws are prefixes of larger ones.

---

## 12. `tests/`: the test files

Run them with `cargo test --release --manifest-path rust/Cargo.toml`.
Each `#[test]` function is one test. `assert!`, `assert_eq!` and
`.unwrap()` make the test fail (panic) when something is wrong. Unit
tests inside the `src/` files (the `#[cfg(test)] mod tests` blocks) run
at the same time.

### 12.1 `tests/tours_and_operators.rs`

**The fixture.** `struct Fixture` is a small random Euclidean instance:
1,000 × 1,000 integer coordinates, with the matrix, 10-nearest lists and
alpha lists built.
- `problem::<C>()` returns a `Problem` with the cache on or off.
- `random_order(seed)` gives a random tour.

The tests use sizes 12, 23, 40 and 64 (`SIZES`), small enough for
debug builds.

**The tests.**
- **`layouts_agree_under_random_reversals_and_swaps`**: apply the same
  random reversals and swaps to an `Array` tour and a `Blocks` tour, and
  check that after every step they are the same logical tour and both
  pass `check`. This is the test behind "logical orientation".
- **`reversal_reverses_exactly_the_path`**: after `reverse(a, b)`, the
  path is reversed and nothing else moved.
- **`undo_restores_the_exact_logical_tour`**: after many random changes,
  `undo_to(mark)` gives back exactly the tour at the mark, with the right
  length.
- **`contract(...)`** (a helper) checks the operator contract (section
  8.1) for one operator at every anchor (three repeats for random
  operators):
  - the change is never positive;
  - the returned change equals the real change in length;
  - the tour passes `check`;
  - a zero change leaves the tour logically identical;
  - the returned anchor is the given one.

  It returns the (change, hash) outcomes.
- **`operator_contract_holds_and_layouts_agree`**: runs `contract` for
  every operator on both layouts, and requires identical outcomes.
- **`deterministic_gains_agree_across_storage_and_cache_modes`**: the
  deterministic operators give the same gains with matrix or computed
  storage, and with the cache on or off. The robustness factors change
  only *time*, never results.
- **`work_counters_split_cache_and_matrix_reads`**: with the cache on,
  candidate distances count as cache reads; with it off, as matrix reads;
  the totals agree.
- **`improving_nl_two_opt(...)`** (a helper) and
  **`local_optima_have_no_improving_application`**: after a full descent,
  no application of that local search improves anywhere. This checks the
  descents really reach local optima.
- **`replays_match_on_any_thread_in_any_order`**: applications run in
  forward order on this thread and in backward order on another thread
  give identical results. This is the property that lets measurement A's
  gains run in parallel and be replayed when timed.
- **`operator_names_round_trip`**: names convert back and forth, O21's id
  is `"O21"`, and 8 operators are deterministic.
- **`retirement_keeps_protocol_ids_and_skips_retired_names`**: the ids
  are exactly O1, O5, O7–O19, O21; `index()` matches positions; retired
  names are not operators but parse as "skip"; unknown names are errors.

### 12.2 `tests/external.rs`

Cross-checks against independent programs. Two of them need external
builds, so they are marked `#[ignore]` and run only with
`cargo test --test external -- --ignored` and the environment variables
they name.
- **`reversals_match_concordes_two_level_flipper`** (ignored): sends
  random flips to a small C program (`tools/concorde_flip_harness.c`,
  linked against Concorde's own `flip_two.c`), which prints `next[]`
  after each flip. Our tour must match after every flip.
- **`double_bridge_matches_concordes_four_swap`**: replays Concorde's
  `random_four_swap` kick (its three flips) and checks our
  `double_bridge_move` gives the same cycle and length change. It needs
  nothing external.
- **`lkh_candidates(path)`** (a helper) reads an LKH `.cand` file.
- **`alpha_with_lkh_penalties_matches_lkh`** (ignored): using LKH's own
  π values, LKH's 5 candidates per city must be contained in ours for
  nearly every city.

The Python cross-check (`tests/reference.rs`) was deleted on 2026-11-05.
`DEVIATIONS.md` still describes it in its last bullet; that bullet is
out of date.

---

## 13. `tools/`: shell scripts and the C harness

These are Bash scripts (`set -euo pipefail` makes Bash stop at the first
error, at the use of an undefined variable, and when any part of a pipe
fails).

- **`lock-clock.sh`**: puts the machine in the timing state that
  `machine.rs` checks for. It re-runs itself with `sudo` when needed.
  1. Saves every core's governor, energy preference and frequency floor
     to `/run/teaspoon-clock.state`.
  2. Turns boost off, sets every core to the `performance` governor, and
     pins each core's floor to its ceiling (4.0 GHz).
  3. Saves and forces the GPU power level to `high`, which fixes the
     fabric and memory clocks on this APU.
  4. Saves the SMT setting and turns SMT off.
  5. Verifies everything and fails loudly if anything did not take.

  The state file doubles as the "locked" marker that `e1r-night.sh` looks
  for. Its comments record the E0 findings that made each step
  necessary.
- **`unlock-clock.sh`**: the reverse. It restores SMT first (so the cores
  it brings back get their settings), then the saved per-core values and
  boost. Without a saved state (after a reboot clears `/run`) it falls
  back to sensible defaults.
- **`overnight.sh`**: the 2026-11-02 night chain: measurement A, the
  population replay check, E1R runs and the robustness factors, in
  priority order with time estimates. It is historical, but a model for
  new chains.
- **`e1r-night.sh`**: the 2026-11-05 E1R night chain.
  - **Timing:** a deadline (8 hours by default) with a reserve kept for
    the validations; every timed stage under `timeout`.
  - **Single instance:** a `flock` guard, so only one chain runs.
  - **Clock switching:** flags in `runs/e1r-night/flags/` tell the root
    companion job when to switch the clock.
  - **R3 sizing:** R3 is sized from R1's measured speed by a small
    embedded Python script.
- **`e1r-clock-switch.sh`**: the root companion, started with
  `systemd-run`. It waits for the `calibrations-done` flag and unlocks the
  clock (the rollouts are untimed and run faster unlocked). It then waits
  for `rollouts-done` or `chain-ended` and locks the clock again for
  validation.
- **`concorde_flip_harness.c`**: about 25 lines of C. It reads `n m`,
  then `m` flips; applies each with Concorde's flipper on the identity
  tour; and prints every city's `next` after each flip. `tests/external.rs`
  drives it.

---

## 14. Where to start reading the code yourself

A suggested order, roughly two short sittings each. After each step,
try the small exercise. Run things with `cargo test` and the `aos`
binary.

1. **`src/rng.rs`** (170 lines). It is short and self-contained, and it
   shows structs, `impl`, tests and the `?`-free style of simple code.
   *Exercise:* in the test module, add a test that `unit()` always stays
   in [0, 1).
2. **`src/instance/distance.rs` and `storage.rs`**: traits and generics
   on the smallest possible example (`Dist`, `Matrix`, `Computed`).
3. **`src/tour/mod.rs`, then `tour/array.rs`**: the `Layout` trait, and
   how undo works. Skip `blocks.rs` at first; it is the hardest data
   structure in the crate.
4. **`src/ops/mod.rs`, then `ops/two_opt.rs`**: `Problem`, `Work`,
   `Op::apply`, and the 2-opt search with the neighbour-list stopping
   rule. Everything else in `ops/` is a variation.
   *Exercise:* read `improve_city` and write down, for one direction, the
   four edges involved and why `added >= removed` lets the loop stop.
5. **`src/experiments/common.rs`**, the `Job`/`dispatch` section and
   `sampled`: how run-time options select compiled code, and how a
   microsecond is timed.
6. **`src/experiments/marginal.rs`**, stage 1 only (`GainJob`,
   `measure_gains`): this is measurement A's core loop, and with it you
   can read any results table.
7. **`src/experiments/rollouts.rs`**: `Rollout::run`, then `plan`, then
   `AnalysisRun::write`. These are the parts behind last night's numbers.
   `catch_up` and `slice_rate_advantage` are short and worth checking
   against the journal.

Things you can safely skip on a first read:
- the details of `tour/blocks.rs`;
- most of `e0.rs`, apart from the gate rules at its top;
- `timing/counters.rs` and `timing/disturb.rs`.

They protect the measurements but do not change what is measured.
