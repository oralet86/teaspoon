//! Core pinning for timed work (Linux), a port of `aos/cores.py`.
//!
//! Timed workers must not share a physical core: two hyper-threads on one
//! core slow each other down.

use std::path::Path;

use anyhow::{Context, bail};

const TOPOLOGY: &str = "/sys/devices/system/cpu";

/// The logical CPUs this process may run on.
///
/// # Errors
///
/// If the affinity mask cannot be read.
pub fn allowed_cpus() -> anyhow::Result<Vec<usize>> {
    use nix::sched::{CpuSet, sched_getaffinity};
    use nix::unistd::Pid;
    let set = sched_getaffinity(Pid::from_raw(0)).context("sched_getaffinity")?;
    Ok((0..CpuSet::count())
        .filter(|&cpu| set.is_set(cpu).unwrap_or(false))
        .collect())
}

/// The SMT siblings of `cpu` (including itself), from sysfs.
///
/// # Errors
///
/// If the topology cannot be read: pinning then cannot keep siblings idle,
/// so the run must not start.
pub fn siblings(cpu: usize) -> anyhow::Result<Vec<usize>> {
    let path = Path::new(TOPOLOGY).join(format!("cpu{cpu}/topology/thread_siblings_list"));
    let text =
        std::fs::read_to_string(&path).with_context(|| format!("reading {}", path.display()))?;
    let mut cpus = Vec::new();
    for part in text.trim().split(',') {
        match part.split_once('-') {
            Some((low, high)) => cpus.extend(low.parse::<usize>()?..=high.parse::<usize>()?),
            None => cpus.push(part.parse()?),
        }
    }
    Ok(cpus)
}

/// One logical CPU per physical core (the lowest-numbered sibling),
/// restricted to the allowed CPUs, quietest first: ordered by the
/// interrupts each has handled since boot ([`interrupts_per_cpu`]).
///
/// Experiments time on the first core and run their untimed main thread
/// on the last.  Device interrupts stay on fixed cores (without
/// irqbalance), and CPU 0 also takes most of the kernel's timer ticks; on
/// 2026-11-02 it also served the USB controller and spent about 3% more
/// user-mode cycles on the same instructions than the other cores for ten
/// minutes, then recovered: the contention gate's closing-phase drift.
///
/// # Errors
///
/// If the affinity or the topology cannot be read.
pub fn physical_cores() -> anyhow::Result<Vec<usize>> {
    let mut seen: Vec<Vec<usize>> = Vec::new();
    let mut chosen = Vec::new();
    for cpu in allowed_cpus()? {
        let group = siblings(cpu)?;
        if !seen.contains(&group) {
            seen.push(group);
            chosen.push(cpu);
        }
    }
    match interrupts_per_cpu() {
        Ok(counts) => {
            chosen.sort_by_key(|cpu| (counts.get(cpu).copied().unwrap_or(u64::MAX), *cpu));
        }
        Err(error) => {
            log::warn!("cores kept in numeric order, interrupt counts unreadable: {error:#}");
        }
    }
    Ok(chosen)
}

/// Interrupts each online CPU has handled since boot (all sources of
/// `/proc/interrupts`: devices, timers, inter-processor calls), indexed by
/// CPU number.
///
/// # Errors
///
/// If the file cannot be read or its header names no CPU.
pub fn interrupts_per_cpu() -> anyhow::Result<std::collections::BTreeMap<usize, u64>> {
    let text = std::fs::read_to_string("/proc/interrupts").context("reading /proc/interrupts")?;
    let mut lines = text.lines();
    let cpus: Vec<usize> = lines
        .next()
        .context("/proc/interrupts is empty")?
        .split_whitespace()
        .filter_map(|column| column.strip_prefix("CPU")?.parse().ok())
        .collect();
    if cpus.is_empty() {
        bail!("/proc/interrupts names no CPU");
    }
    let mut totals: std::collections::BTreeMap<usize, u64> =
        cpus.iter().map(|&cpu| (cpu, 0)).collect();
    for line in lines {
        let Some((_, counts)) = line.split_once(':') else {
            continue;
        };
        // One count per CPU column, then the source's description; a few
        // lines (ERR, MIS) have a single machine-wide count, ignored.
        let counts: Vec<u64> = counts
            .split_whitespace()
            .map_while(|token| token.parse().ok())
            .collect();
        if counts.len() != cpus.len() {
            continue;
        }
        for (cpu, count) in cpus.iter().zip(counts) {
            *totals.entry(*cpu).or_default() += count;
        }
    }
    Ok(totals)
}

/// Restricts the calling thread to logical CPU `cpu`.
///
/// # Errors
///
/// If the kernel refuses the affinity.
pub fn pin_current_thread(cpu: usize) -> anyhow::Result<()> {
    use nix::sched::{CpuSet, sched_setaffinity};
    use nix::unistd::Pid;
    let mut set = CpuSet::new();
    set.set(cpu)
        .with_context(|| format!("cpu {cpu} out of range"))?;
    sched_setaffinity(Pid::from_raw(0), &set)
        .with_context(|| format!("sched_setaffinity({cpu}) failed"))
}
