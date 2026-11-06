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
/// restricted to the allowed CPUs.
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
    Ok(chosen)
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
