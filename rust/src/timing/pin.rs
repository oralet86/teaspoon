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
    // SAFETY: an all-zero cpu_set_t is a valid empty set.
    let mut set: libc::cpu_set_t = unsafe { std::mem::zeroed() };
    // SAFETY: `set` is a valid, writable cpu_set_t of the size passed.
    let status =
        unsafe { libc::sched_getaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &mut set) };
    if status != 0 {
        bail!(
            "sched_getaffinity failed: {}",
            std::io::Error::last_os_error()
        );
    }
    Ok((0..libc::CPU_SETSIZE as usize)
        // SAFETY: `cpu` is below CPU_SETSIZE, the size of the set.
        .filter(|&cpu| unsafe { libc::CPU_ISSET(cpu, &set) })
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
    // SAFETY: an all-zero cpu_set_t is a valid empty set; CPU_SET writes
    // inside it for cpu < CPU_SETSIZE, which is checked first.
    let mut set: libc::cpu_set_t = unsafe { std::mem::zeroed() };
    if cpu >= libc::CPU_SETSIZE as usize {
        bail!("cpu {cpu} out of range");
    }
    unsafe { libc::CPU_SET(cpu, &mut set) };
    // SAFETY: pid 0 is the calling thread; `set` is a valid cpu_set_t.
    let status =
        unsafe { libc::sched_setaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &set) };
    if status != 0 {
        bail!(
            "sched_setaffinity({cpu}) failed: {}",
            std::io::Error::last_os_error()
        );
    }
    Ok(())
}
