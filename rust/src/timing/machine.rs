//! Machine-state checks and logging (`ideas/rust-port.md` §7.2).
//!
//! Timed runs need boost off, the `performance` governor, the frequency
//! floor pinned to the ceiling, and mains power.
//! The tool does not change these settings itself (that needs root); it
//! checks them before starting and stops unless explicitly overridden.

use std::path::Path;

use anyhow::{Context, bail};
use serde::Serialize;

const CPUS: &str = "/sys/devices/system/cpu";

fn read_trimmed(path: impl AsRef<Path>) -> Option<String> {
    std::fs::read_to_string(path)
        .ok()
        .map(|text| text.trim().to_string())
}

/// The CPU flags of the first processor in `/proc/cpuinfo`.
///
/// # Errors
///
/// If the file cannot be read or has no flags line.
pub fn cpu_flags() -> anyhow::Result<Vec<String>> {
    let text = std::fs::read_to_string("/proc/cpuinfo").context("reading /proc/cpuinfo")?;
    let line = text
        .lines()
        .find(|line| line.starts_with("flags"))
        .context("/proc/cpuinfo has no flags line")?;
    let (_, flags) = line.split_once(':').context("malformed flags line")?;
    Ok(flags.split_whitespace().map(str::to_string).collect())
}

/// The CPU model name, if `/proc/cpuinfo` has one.
pub fn cpu_model() -> Option<String> {
    let text = std::fs::read_to_string("/proc/cpuinfo").ok()?;
    let line = text.lines().find(|line| line.starts_with("model name"))?;
    Some(line.split_once(':')?.1.trim().to_string())
}

/// The running kernel release.
pub fn kernel() -> Option<String> {
    read_trimmed("/proc/sys/kernel/osrelease")
}

/// Global boost switch: `Some(true)` when boost is enabled.
pub fn boost() -> Option<bool> {
    read_trimmed(format!("{CPUS}/cpufreq/boost")).map(|value| value == "1")
}

/// The scaling governor of `cpu`.
pub fn governor(cpu: usize) -> Option<String> {
    read_trimmed(format!("{CPUS}/cpu{cpu}/cpufreq/scaling_governor"))
}

/// The frequency floor and ceiling the governor may use on `cpu` (kHz),
/// `scaling_min_freq` and `scaling_max_freq`.
pub fn frequency_limits(cpu: usize) -> (Option<u64>, Option<u64>) {
    let read = |name: &str| {
        read_trimmed(format!("{CPUS}/cpu{cpu}/cpufreq/{name}")).and_then(|value| value.parse().ok())
    };
    (read("scaling_min_freq"), read("scaling_max_freq"))
}

/// The kernel's recent average frequency of `cpu` in kHz (APERF/MPERF over
/// the last tick), when the driver exposes it.
pub fn average_khz(cpu: usize) -> Option<u64> {
    read_trimmed(format!("{CPUS}/cpu{cpu}/cpufreq/cpuinfo_avg_freq"))?
        .parse()
        .ok()
}

/// Package temperature in °C from the `k10temp` (AMD) or `coretemp`
/// (Intel) hwmon driver.
pub fn temperature_c() -> Option<f64> {
    for entry in std::fs::read_dir("/sys/class/hwmon").ok()?.flatten() {
        let name = read_trimmed(entry.path().join("name"));
        if matches!(name.as_deref(), Some("k10temp" | "coretemp")) {
            let milli: f64 = read_trimmed(entry.path().join("temp1_input"))?
                .parse()
                .ok()?;
            return Some(milli / 1000.0);
        }
    }
    None
}

/// Whether the machine runs on mains power; `None` when no supply reports
/// (a desktop without a battery counts as mains).
pub fn on_mains() -> Option<bool> {
    let mut found = None;
    for entry in std::fs::read_dir("/sys/class/power_supply").ok()?.flatten() {
        if read_trimmed(entry.path().join("type")).as_deref() == Some("Mains") {
            let online = read_trimmed(entry.path().join("online")).as_deref() == Some("1");
            found = Some(found.unwrap_or(false) || online);
        }
    }
    found
}

/// One reading of the machine state.
#[derive(Debug, Clone, Serialize)]
pub struct MachineState {
    /// `CLOCK_MONOTONIC_RAW` nanoseconds when read.
    pub monotonic_ns: u64,
    /// Boost enabled.
    pub boost: Option<bool>,
    /// Governor of every CPU in use.
    pub governors: Vec<(usize, Option<String>)>,
    /// Average frequency of every CPU in use, kHz.
    pub average_khz: Vec<(usize, Option<u64>)>,
    /// Frequency floor and ceiling of every CPU in use, kHz.
    pub frequency_limits: Vec<(usize, Option<u64>, Option<u64>)>,
    /// Package temperature, °C.
    pub temperature_c: Option<f64>,
    /// On mains power.
    pub on_mains: Option<bool>,
}

impl MachineState {
    /// Reads the state of the CPUs `cpus`.
    pub fn read(cpus: &[usize]) -> Self {
        Self {
            monotonic_ns: super::tsc::monotonic_raw_ns(),
            boost: boost(),
            governors: cpus.iter().map(|&cpu| (cpu, governor(cpu))).collect(),
            average_khz: cpus.iter().map(|&cpu| (cpu, average_khz(cpu))).collect(),
            frequency_limits: cpus
                .iter()
                .map(|&cpu| {
                    let (floor, ceiling) = frequency_limits(cpu);
                    (cpu, floor, ceiling)
                })
                .collect(),
            temperature_c: temperature_c(),
            on_mains: on_mains(),
        }
    }

    /// What prevents a timed run on these CPUs (empty when none).
    pub fn problems(&self) -> Vec<String> {
        let mut problems = Vec::new();
        match self.boost {
            Some(false) => {}
            Some(true) => problems.push("boost is enabled".to_string()),
            None => problems.push("boost state unknown".to_string()),
        }
        for (cpu, governor) in &self.governors {
            if governor.as_deref() != Some("performance") {
                problems.push(format!(
                    "cpu{cpu} governor is {governor:?}, not performance"
                ));
            }
        }
        // A floor below the ceiling lets a core slow down whenever it is
        // briefly idle (between states, while the main thread writes), so
        // the next timed work starts at a lower frequency.
        for (cpu, floor, ceiling) in &self.frequency_limits {
            match (floor, ceiling) {
                (Some(floor), Some(ceiling)) if floor == ceiling => {}
                (Some(floor), Some(ceiling)) => problems.push(format!(
                    "cpu{cpu} frequency floor {floor} kHz is below its ceiling {ceiling} kHz (pin it: scaling_min_freq = scaling_max_freq)"
                )),
                _ => problems.push(format!("cpu{cpu} frequency limits unknown")),
            }
        }
        if self.on_mains == Some(false) {
            problems.push("running on battery".to_string());
        }
        problems
    }
}

/// Stops unless the machine is locked for timing, or `allow_unlocked` is
/// set, in which case the problems are logged and returned for provenance.
///
/// # Errors
///
/// Listing every problem, when the machine is not locked and no override
/// was given.
pub fn require_locked(cpus: &[usize], allow_unlocked: bool) -> anyhow::Result<Vec<String>> {
    let mut problems = MachineState::read(cpus).problems();
    if let Err(error) = super::cycles::ThreadCounters::open() {
        problems.push(format!(
            "core-cycle counter unavailable, frequency drift cannot be checked: {error:#}"
        ));
    }
    if problems.is_empty() {
        return Ok(problems);
    }
    if !allow_unlocked {
        bail!(
            "machine not locked for timing: {}; fix it or pass --allow-unlocked-clock",
            problems.join("; ")
        );
    }
    for problem in &problems {
        log::warn!("timing on an unlocked machine: {problem}");
    }
    Ok(problems)
}
