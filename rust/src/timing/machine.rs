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

/// The CPU flags of the first processor in `/proc/cpuinfo` (`procfs`).
///
/// # Errors
///
/// If the file cannot be read or has no flags line.
pub fn cpu_flags() -> anyhow::Result<Vec<String>> {
    use procfs::Current;
    let info = procfs::CpuInfo::current().context("reading /proc/cpuinfo")?;
    let flags = info.flags(0).context("/proc/cpuinfo has no flags line")?;
    Ok(flags.into_iter().map(str::to_string).collect())
}

/// The CPU model name, if `/proc/cpuinfo` has one (`procfs`).
pub fn cpu_model() -> Option<String> {
    use procfs::Current;
    procfs::CpuInfo::current()
        .ok()?
        .model_name(0)
        .map(str::to_string)
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
fn frequency_limits(cpu: usize) -> (Option<u64>, Option<u64>) {
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

/// [`average_khz`] as a float, the form the drift checks compare.
pub fn average_khz_f64(cpu: usize) -> Option<f64> {
    average_khz(cpu).map(|value| value as f64)
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

/// The current level of an `amdgpu` power-management clock (`fclk`, the
/// data fabric; `mclk`, memory; `socclk`), MHz, from the line marked `*`
/// of `pp_dpm_<clock>`.  On an APU these clocks are shared with the CPU
/// and change with load unless the GPU's performance level is forced
/// (`power_dpm_force_performance_level`, root), so memory-bound timings
/// can depend on them.  `None` without an `amdgpu` device.
pub fn soc_clock_mhz(clock: &str) -> Option<u32> {
    for entry in std::fs::read_dir("/sys/class/drm").ok()?.flatten() {
        let path = entry.path().join("device").join(format!("pp_dpm_{clock}"));
        let Ok(text) = std::fs::read_to_string(&path) else {
            continue;
        };
        let line = text.lines().find(|line| line.trim_end().ends_with('*'))?;
        let digits: String = line
            .split(':')
            .nth(1)?
            .chars()
            .filter(char::is_ascii_digit)
            .collect();
        return digits.parse().ok();
    }
    None
}

/// The amdgpu power level of the first device that has one.
fn soc_power_level() -> Option<String> {
    for entry in std::fs::read_dir("/sys/class/drm").ok()?.flatten() {
        if let Some(level) = read_trimmed(
            entry
                .path()
                .join("device/power_dpm_force_performance_level"),
        ) {
            return Some(level);
        }
    }
    None
}

/// Whether the machine runs on mains power; `None` when no supply reports
/// (a desktop without a battery counts as mains).
fn on_mains() -> Option<bool> {
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
    /// Data-fabric clock, MHz (APU; see [`soc_clock_mhz`]).
    pub fclk_mhz: Option<u32>,
    /// Memory clock, MHz (APU; see [`soc_clock_mhz`]).
    pub mclk_mhz: Option<u32>,
    /// The amdgpu power level (`power_dpm_force_performance_level`);
    /// `None` without an amdgpu device.
    pub soc_power_level: Option<String>,
    /// SMT control (`on`, `off`, `forceoff`, `notsupported`, …).
    pub smt: Option<String>,
    /// On mains power.
    pub on_mains: Option<bool>,
}

impl MachineState {
    /// Reads the state of the CPUs `cpus`.
    pub fn read(cpus: &[usize]) -> Self {
        Self {
            monotonic_ns: super::monotonic_raw_ns(),
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
            fclk_mhz: soc_clock_mhz("fclk"),
            mclk_mhz: soc_clock_mhz("mclk"),
            soc_power_level: soc_power_level(),
            smt: read_trimmed(format!("{CPUS}/smt/control")),
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
        // On an APU the data-fabric and memory clocks follow load unless
        // the GPU's power level is forced; memory-bound timings then depend
        // on how busy the rest of the chip is (E0, 2026-11-02).
        if let Some(level) = &self.soc_power_level
            && level != "high"
        {
            problems.push(format!(
                "amdgpu power level is {level}, not high (fabric and memory clocks follow load)"
            ));
        }
        // A task on the timed core's SMT sibling slows it by up to half.
        if self.smt.as_deref() == Some("on") {
            problems.push("SMT is on (tasks on a timed core's sibling slow it)".to_string());
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
    if let Err(error) = super::counters::check() {
        problems.push(format!(
            "instruction and cycle counts of timed regions unavailable: {error}"
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
