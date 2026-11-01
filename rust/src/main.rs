//! The `aos` command-line tool: one subcommand per experiment
//! (`ideas/rust-port.md` §3.1).

use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

use anyhow::Context;
use clap::{Parser, Subcommand};

use aos::experiments::{common, e0, marginal, population, rollouts, states};
use aos::instance::Instance;
use aos::instance::alpha::CACHE_DIRECTORY;
use aos::instance::reference;
use aos::timing::{machine, pin, tsc};

/// Operator-selection experiments for the symmetric TSP.
#[derive(Parser)]
#[command(name = "aos", version)]
struct Cli {
    /// Repository root (instances, caches and `runs/` are resolved from it).
    #[arg(long, global = true, default_value = concat!(env!("CARGO_MANIFEST_DIR"), "/.."))]
    repository: PathBuf,
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Load instances and print their size, optimum and candidate status.
    Instance {
        /// TSPLIB files.
        #[arg(required = true)]
        paths: Vec<PathBuf>,
    },
    /// Compute and cache alpha-nearness candidates (untimed, parallel).
    Candidates {
        /// TSPLIB files.
        #[arg(required = true)]
        paths: Vec<PathBuf>,
        /// Parallel workers (default: every logical CPU).
        #[arg(long)]
        workers: Option<usize>,
    },
    /// Compare distances and candidate lists with the Python reference
    /// (`rust/tools/export_reference.py`); fails on any exact mismatch.
    Verify {
        /// Directory written by the export script.
        reference: PathBuf,
    },
    /// Calibrate the TSC clock and report the machine state.
    Clock,
    /// E0-rs: clock, distance-read and flip costs, cost per application,
    /// O7 descents and the contention gate (`ideas/rust-port.md` §8.1).
    E0 {
        /// A dry run of a few seconds that exercises every step.
        #[arg(long)]
        quick: bool,
        /// Time even if boost is on or the governor is not `performance`
        /// (recorded in provenance and in the run directory's name).
        #[arg(long)]
        allow_unlocked_clock: bool,
        /// Run directory (default: `runs/e0-rs/<timestamp>`).
        #[arg(long)]
        out: Option<PathBuf>,
        /// Worker counts of the contention phases, in order (default
        /// 1,2,4,6,7,1).  The largest needs that many physical cores plus
        /// one for the main thread among the CPUs the run may use (restrict
        /// those with `taskset` or a cgroup to leave cores free).
        #[arg(long, value_delimiter = ',')]
        contention_workers: Option<Vec<usize>>,
    },
    /// Build E1 states by local-optimality level (untimed, parallel).
    States {
        /// Instance paths relative to the repository root.
        #[arg(long, required = true, num_args = 1..)]
        instances: Vec<String>,
        /// State seeds.
        #[arg(long, num_args = 1.., default_values_t = [0, 1, 2])]
        seeds: Vec<u64>,
        /// Instances larger than this get only the first seed.
        #[arg(long, default_value_t = 5000)]
        single_seed_above: usize,
        /// Worker threads (0: every CPU).
        #[arg(long, default_value_t = 0)]
        workers: usize,
        /// Run directory (default: `runs/e1-states-rs/<timestamp>`).
        #[arg(long)]
        out: Option<PathBuf>,
    },
    /// Measurement A: marginal rates per state (gains, times, summarise).
    Measure(MeasureArgs),
    /// E1R rollouts for Claim 1.4 (plan, calibrate, run, validate, analyse).
    E1r(E1rArgs),
    /// Population runs (E2/E3 and the greedy-vs-softmax pilot).
    Population(PopulationArgs),
    /// Compare two measurement-A results per state (robustness factors;
    /// the Python reference when one side is a `cells.csv`).
    Compare {
        /// Main result: a measurement directory or a Python `cells.csv`.
        main: PathBuf,
        /// Alternative result, same kinds.
        alternative: PathBuf,
        /// Output path without extension (`.parquet` and `.md` are written).
        #[arg(long)]
        out: PathBuf,
    },
    /// Print a Parquet table as CSV.
    Show {
        /// The Parquet file.
        path: PathBuf,
        /// Columns to print (all when omitted).
        #[arg(long, num_args = 1..)]
        columns: Vec<String>,
    },
}

/// Options of `measure`.
#[derive(clap::Args)]
struct MeasureArgs {
    /// Directory of states files (`<run>/states`, Rust- or Python-built).
    #[arg(long)]
    states: String,
    /// Run directory (created on first use; later stages reuse it).
    #[arg(long)]
    out: PathBuf,
    /// Which stage(s) to run.
    #[arg(long, value_enum, default_value_t = marginal::Stage::All)]
    stage: marginal::Stage,
    /// Settings preset.
    #[arg(long, value_enum, default_value_t = Preset::Budgeted)]
    preset: Preset,
    /// Pass-1 repeats of the random operators.
    #[arg(long)]
    repeats: Option<usize>,
    /// Timing budget per state, seconds (0: none, every anchor).
    #[arg(long)]
    state_seconds: Option<f64>,
    /// Fewest timed rounds.
    #[arg(long)]
    min_rounds: Option<usize>,
    /// Most timed rounds.
    #[arg(long)]
    max_rounds: Option<usize>,
    /// Target precision of mean times.
    #[arg(long)]
    precision: Option<f64>,
    /// Instance names to include.
    #[arg(long, num_args = 0..)]
    instances: Option<Vec<String>>,
    /// State seeds to include.
    #[arg(long, num_args = 0..)]
    seeds: Option<Vec<u64>>,
    /// State names to include.
    #[arg(long, num_args = 0..)]
    stages: Option<Vec<String>>,
    /// Distance storage (robustness factor).
    #[arg(long, value_enum, default_value_t = common::Storage::Matrix)]
    storage: common::Storage,
    /// Read candidate distances from the matrix (robustness factor).
    #[arg(long)]
    no_cache: bool,
    /// Tour representation (robustness factor).
    #[arg(long, value_enum, default_value_t = common::LayoutChoice::Auto)]
    layout: common::LayoutChoice,
    /// Size from which `auto` uses the two-level list (from E0-rs).
    #[arg(long, default_value_t = 1000)]
    n_split: usize,
    /// Reuse the anchors of this measurement run (robustness factors).
    #[arg(long)]
    anchors_from: Option<PathBuf>,
    /// Untimed threads for gains and summaries (0: every CPU).
    #[arg(long, default_value_t = 0)]
    gain_workers: usize,
    /// Pinned timed workers (from the E0-rs contention gate).
    #[arg(long, default_value_t = 1)]
    timed_workers: usize,
    /// Time even if boost is on or the governor is not `performance`.
    #[arg(long)]
    allow_unlocked_clock: bool,
}

/// Options of `e1r`.
#[derive(clap::Args)]
struct E1rArgs {
    /// Run directory.
    #[arg(long)]
    out: PathBuf,
    /// Stage.
    #[arg(long, value_enum)]
    stage: E1rStage,
    /// Measurement-A run directory (plan only).
    #[arg(long)]
    measurement: Option<String>,
    /// Instances per (family, size) cell (plan only).
    #[arg(long, default_value_t = 2)]
    instances_per_cell: usize,
    /// States per instance and class (plan only).
    #[arg(long, default_value_t = 2)]
    states_per_class: usize,
    /// First operators per state (plan only).
    #[arg(long, default_value_t = 5)]
    candidates: usize,
    /// Paired rollouts per (state, candidate) (plan only).
    #[arg(long, default_value_t = 32)]
    repeats: usize,
    /// Virtual slice length per city, ns (plan only).  0.01 ms holds about
    /// as many Rust applications as the Python design's 0.1 ms slice did
    /// (spec §8.5).
    #[arg(long, default_value_t = 10_000.0)]
    slice_ns_per_city: f64,
    /// Horizon in slices (plan only).
    #[arg(long, default_value_t = 64)]
    horizon_slices: usize,
    /// Calibration horizon in slices (plan only).
    #[arg(long, default_value_t = 16)]
    calibration_slices: usize,
    /// Continuation policy on a slice without gain (plan only; decision D1).
    #[arg(long, value_enum, default_value_t = rollouts::StallRule::None)]
    stall_rule: rollouts::StallRule,
    /// Rollouts replayed with real timing (plan or validate).
    #[arg(long)]
    validate_rollouts: Option<usize>,
    /// Untimed threads of the run stage (0: every CPU).
    #[arg(long, default_value_t = 0)]
    workers: usize,
    /// Time even if boost is on or the governor is not `performance`.
    #[arg(long)]
    allow_unlocked_clock: bool,
}

/// Stages of `e1r`.
#[derive(Clone, Copy, clap::ValueEnum)]
enum E1rStage {
    /// Choose states and candidates; fit the provisional clock.
    Plan,
    /// Refit the clock on timed rollouts (one pinned core).
    Calibrate,
    /// Every rollout, untimed, on every CPU.
    Run,
    /// Replay some rollouts with real timing.
    Validate,
    /// Tables and summary.
    Analyse,
}

fn e1r(repository: &Path, args: E1rArgs) -> anyhow::Result<()> {
    let directory = resolve(repository, &args.out);
    match args.stage {
        E1rStage::Plan => {
            let settings = rollouts::Settings {
                measurement: args
                    .measurement
                    .ok_or_else(|| anyhow::anyhow!("--measurement is required at plan"))?,
                instances_per_cell: args.instances_per_cell,
                states_per_class: args.states_per_class,
                candidates: args.candidates,
                repeats: args.repeats,
                slice_ns_per_city: args.slice_ns_per_city,
                horizon_slices: args.horizon_slices,
                fit_seeds: vec![0, 1, 2],
                check_seed: 3,
                seed: 0,
                validate_rollouts: args.validate_rollouts.unwrap_or(24),
                calibration_slices: args.calibration_slices,
                stall_rule: args.stall_rule,
            };
            rollouts::plan(repository, &directory, &settings)
        }
        E1rStage::Calibrate => {
            rollouts::calibrate(repository, &directory, args.allow_unlocked_clock)
        }
        E1rStage::Run => rollouts::run(repository, &directory, args.workers),
        E1rStage::Validate => rollouts::validate(
            repository,
            &directory,
            args.validate_rollouts,
            args.allow_unlocked_clock,
        ),
        E1rStage::Analyse => rollouts::analyse(repository, &directory),
    }
}

/// Options of `population`.
#[derive(clap::Args)]
struct PopulationArgs {
    /// Instance paths relative to the repository root.
    #[arg(long, required = true, num_args = 1..)]
    instances: Vec<String>,
    /// Arms: fixed:<operator>, uniform, pm, ap, greedy, softmax:<tau>.
    #[arg(long, required = true, num_args = 1..)]
    arms: Vec<String>,
    /// Run seeds, paired across arms.
    #[arg(long, num_args = 1.., default_values_t = [0])]
    seeds: Vec<u64>,
    /// Population size.
    #[arg(long, default_value_t = 20)]
    size: usize,
    /// Tournament size.
    #[arg(long, default_value_t = 3)]
    tournament: usize,
    /// Slice length in seconds.
    #[arg(long)]
    slice_seconds: Option<f64>,
    /// Applications per slice (count mode).
    #[arg(long)]
    slice_applications: Option<u64>,
    /// Wall-clock budget per run.
    #[arg(long)]
    budget_seconds: Option<f64>,
    /// Application budget per run.
    #[arg(long)]
    budget_applications: Option<u64>,
    /// Slice budget per run.
    #[arg(long)]
    budget_slices: Option<u64>,
    /// Measurement-A cells table for greedy and softmax.
    #[arg(long)]
    rates: Option<String>,
    /// Size from which the two-level list is used (from E0-rs).
    #[arg(long, default_value_t = 1000)]
    n_split: usize,
    /// Pinned timed workers (from the E0-rs contention gate).
    #[arg(long, default_value_t = 1)]
    timed_workers: usize,
    /// Replay every run in count mode and require the same best tour.
    #[arg(long)]
    check_replay: bool,
    /// Run directory.
    #[arg(long)]
    out: PathBuf,
    /// Time even if boost is on or the governor is not `performance`.
    #[arg(long)]
    allow_unlocked_clock: bool,
}

fn population_runs(repository: &Path, args: PopulationArgs) -> anyhow::Result<()> {
    let settings = population::Settings {
        instances: args.instances,
        arms: args
            .arms
            .iter()
            .map(|arm| population::ArmSpec::parse(arm))
            .collect::<anyhow::Result<_>>()?,
        seeds: args.seeds,
        config: population::Config {
            size: args.size,
            tournament: args.tournament,
            slice_seconds: args.slice_seconds,
            slice_applications: args.slice_applications,
            budget_seconds: args.budget_seconds,
            budget_applications: args.budget_applications,
            budget_slices: args.budget_slices,
        },
        rates: args.rates,
        implementation: common::Implementation {
            n_split: args.n_split,
            ..common::Implementation::default()
        },
        check_replay: args.check_replay,
    };
    population::run(
        repository,
        &resolve(repository, &args.out),
        &settings,
        args.timed_workers,
        args.allow_unlocked_clock,
    )
}

/// Settings presets of `measure`.
#[derive(Clone, Copy, clap::ValueEnum)]
enum Preset {
    /// Every anchor, no time cap.
    Full,
    /// The E1 main-run design: budget per state, early states skipped above n = 1000.
    Budgeted,
    /// Three states of kroA100.
    Quick,
}

fn measure(repository: &Path, args: MeasureArgs) -> anyhow::Result<()> {
    let mut settings = match args.preset {
        Preset::Full => marginal::Settings::full(args.states),
        Preset::Budgeted => marginal::Settings::budgeted(args.states),
        Preset::Quick => marginal::Settings::quick(args.states),
    };
    if let Some(value) = args.repeats {
        settings.repeats = value;
    }
    if let Some(value) = args.state_seconds {
        settings.state_seconds = (value > 0.0).then_some(value);
    }
    if let Some(value) = args.min_rounds {
        settings.min_rounds = value;
    }
    if let Some(value) = args.max_rounds {
        settings.max_rounds = value;
    }
    if let Some(value) = args.precision {
        settings.precision = value;
    }
    if let Some(value) = args.instances {
        settings.instances = value;
    }
    if let Some(value) = args.seeds {
        settings.seeds = value;
    }
    if let Some(value) = args.stages {
        settings.stages = value;
    }
    anyhow::ensure!(
        2 <= settings.min_rounds && settings.min_rounds <= settings.max_rounds,
        "need 2 <= min_rounds <= max_rounds"
    );
    settings.implementation = common::Implementation {
        storage: args.storage,
        cache: !args.no_cache,
        layout: args.layout,
        n_split: args.n_split,
    };
    settings.anchors_from = args
        .anchors_from
        .map(|path| resolve(repository, &path).display().to_string());
    marginal::run(
        repository,
        &resolve(repository, &args.out),
        &settings,
        args.stage,
        args.gain_workers,
        args.timed_workers,
        args.allow_unlocked_clock,
    )
}

fn resolve(repository: &Path, path: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        repository.join(path)
    }
}

fn instance_info(repository: &Path, paths: &[PathBuf]) -> anyhow::Result<()> {
    let cache = repository.join(CACHE_DIRECTORY);
    for path in paths {
        let instance = Instance::load(&resolve(repository, path), &cache)?;
        println!(
            "{}\tn={}\toptimum={}\tcoordinates={}\talpha={}",
            instance.name,
            instance.n(),
            instance
                .optimum
                .map_or("unknown".to_string(), |value| value.to_string()),
            instance
                .computed
                .as_ref()
                .map_or("none".to_string(), |computed| format!(
                    "{:?}",
                    computed.kind()
                )),
            if instance.alpha.is_some() {
                "cached"
            } else {
                "missing"
            },
        );
    }
    Ok(())
}

fn candidates(repository: &Path, paths: &[PathBuf], workers: Option<usize>) -> anyhow::Result<()> {
    let cache = repository.join(CACHE_DIRECTORY);
    let workers = workers
        .unwrap_or_else(|| std::thread::available_parallelism().map_or(1, usize::from))
        .clamp(1, paths.len());
    let next = AtomicUsize::new(0);
    let errors = Mutex::new(Vec::new());
    std::thread::scope(|scope| {
        for _ in 0..workers {
            scope.spawn(|| {
                loop {
                    let index = next.fetch_add(1, Ordering::Relaxed);
                    let Some(path) = paths.get(index) else { break };
                    let started = std::time::Instant::now();
                    let result = Instance::load(&resolve(repository, path), &cache).and_then(
                        |mut instance| {
                            let computed = instance.ensure_alpha(&cache)?;
                            Ok((instance.name, computed))
                        },
                    );
                    match result {
                        Ok((name, true)) => log::info!(
                            "{name}: alpha candidates computed in {:.1} s",
                            started.elapsed().as_secs_f64()
                        ),
                        Ok((name, false)) => log::info!("{name}: alpha candidates cached already"),
                        Err(error) => errors
                            .lock()
                            .expect("no worker panics while holding the lock")
                            .push(format!("{}: {error:#}", path.display())),
                    }
                }
            });
        }
    });
    let errors = errors.into_inner().expect("workers finished");
    if !errors.is_empty() {
        anyhow::bail!(
            "{} instance(s) failed:\n{}",
            errors.len(),
            errors.join("\n")
        );
    }
    Ok(())
}

fn verify(repository: &Path, reference: &Path) -> anyhow::Result<()> {
    let cache = repository.join(CACHE_DIRECTORY);
    let mut failed = Vec::new();
    for file in reference::reference_files(reference)? {
        let report = reference::verify(&file, repository, &cache)?;
        println!(
            "{}\tmatrix mismatches={}\tneighbour mismatches={}\talpha agreement={}",
            report.name,
            report.matrix_mismatches,
            report.neighbour_mismatches,
            report
                .alpha_agreement
                .map_or("n/a".to_string(), |share| format!("{:.1}%", 100.0 * share)),
        );
        if !report.passed() {
            failed.push(report.name);
        }
    }
    if !failed.is_empty() {
        anyhow::bail!("reference mismatch for {failed:?}");
    }
    println!("all matrices and neighbour lists match the Python reference");
    Ok(())
}

fn clock() -> anyhow::Result<()> {
    let clock = tsc::Clock::calibrate(5, 200)?;
    println!("{}", serde_json::to_string_pretty(&clock)?);
    let cores = pin::physical_cores()?;
    println!("physical cores: {cores:?}");
    let state = machine::MachineState::read(&cores);
    println!("{}", serde_json::to_string_pretty(&state)?);
    let problems = state.problems();
    if problems.is_empty() {
        println!("machine locked for timing");
    } else {
        println!("NOT locked for timing: {}", problems.join("; "));
    }
    Ok(())
}

fn main() -> anyhow::Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    let cli = Cli::parse();
    let repository = cli
        .repository
        .canonicalize()
        .with_context(|| format!("repository {}", cli.repository.display()))?;
    match cli.command {
        Command::Instance { paths } => instance_info(&repository, &paths),
        Command::Candidates { paths, workers } => candidates(&repository, &paths, workers),
        Command::Verify { reference } => verify(&repository, &reference),
        Command::Clock => clock(),
        Command::E0 {
            quick,
            allow_unlocked_clock,
            out,
            contention_workers,
        } => {
            let mut settings = if quick {
                e0::Settings::quick()
            } else {
                e0::Settings::full()
            };
            if let Some(workers) = contention_workers {
                anyhow::ensure!(
                    workers.first() == Some(&1) && workers.last() == Some(&1),
                    "contention phases must start and end with 1 worker (baseline and drift)"
                );
                settings.contention_workers = workers;
            }
            e0::run(
                &repository,
                &settings,
                allow_unlocked_clock,
                out.map(|path| resolve(&repository, &path)),
            )
            .map(|_| ())
        }
        Command::States {
            instances,
            seeds,
            single_seed_above,
            workers,
            out,
        } => states::run(
            &repository,
            &states::Settings {
                instances,
                seeds,
                single_seed_above,
                workers,
            },
            out.map(|path| resolve(&repository, &path)),
        )
        .map(|directory| log::info!("states written to {}", directory.display())),
        Command::Measure(args) => measure(&repository, args),
        Command::E1r(args) => e1r(&repository, args),
        Command::Population(args) => population_runs(&repository, args),
        Command::Compare {
            main,
            alternative,
            out,
        } => aos::experiments::compare::compare(
            &resolve(&repository, &main),
            &resolve(&repository, &alternative),
            &resolve(&repository, &out),
        ),
        Command::Show { path, columns } => {
            let batch = aos::io::parquet::read(&resolve(&repository, &path))?;
            aos::io::parquet::write_csv(&batch, &columns, &mut std::io::stdout().lock())
        }
    }
}
