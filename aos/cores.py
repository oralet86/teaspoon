"""Core pinning for timed runs (Linux).

Timed runs must not share a physical core: two hyper-threads on one core
slow each other down.  :func:`physical_cores` lists one logical CPU per
physical core, and :func:`pin_to` restricts the calling process to one CPU.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)

_TOPOLOGY = Path("/sys/devices/system/cpu")


def physical_cores() -> list[int]:
    """Return one logical CPU per physical core (the lowest-numbered sibling),
    restricted to the CPUs this process may run on.

    Falls back to all allowed CPUs when the topology cannot be read.
    """
    allowed = sorted(os.sched_getaffinity(0))
    chosen: list[int] = []
    seen: set[str] = set()
    for cpu in allowed:
        siblings_file = _TOPOLOGY / f"cpu{cpu}" / "topology" / "thread_siblings_list"
        try:
            siblings = siblings_file.read_text().strip()
        except OSError:
            LOGGER.warning("no CPU topology for cpu%d; using all allowed CPUs", cpu)
            return allowed
        if siblings not in seen:
            seen.add(siblings)
            chosen.append(cpu)
    return chosen


def pin_to(cpu: int) -> None:
    """Restrict the calling process to logical CPU ``cpu``."""
    os.sched_setaffinity(0, {cpu})


def _read(path: Path) -> str | None:
    """Return a sysfs value, or ``None`` when the file is missing or unreadable."""
    try:
        return path.read_text().strip()
    except OSError:
        return None


def average_khz(cpu: int) -> int | None:
    """Return the kernel's recent average frequency of ``cpu`` in kHz.

    ``cpuinfo_avg_freq`` is measured from the APERF/MPERF counters over the
    last tick, so it shows the clock a busy core actually ran at; ``None`` on
    kernels or drivers without it.
    """
    value = _read(_TOPOLOGY / f"cpu{cpu}" / "cpufreq" / "cpuinfo_avg_freq")
    return int(value) if value is not None and value.isdigit() else None


def clock_state(cpus: list[int]) -> dict[str, object]:
    """Return the frequency settings that decide whether timings are comparable.

    Records the global boost and SMT switches and, per CPU, the governor,
    energy preference and frequency limits, so a run's provenance shows
    whether the clock was fixed.
    """
    per_cpu = {}
    for cpu in cpus:
        directory = _TOPOLOGY / f"cpu{cpu}" / "cpufreq"
        per_cpu[str(cpu)] = {
            name: _read(directory / name)
            for name in (
                "scaling_driver",
                "scaling_governor",
                "energy_performance_preference",
                "scaling_min_freq",
                "scaling_max_freq",
            )
        }
    return {
        "boost": _read(_TOPOLOGY / "cpufreq" / "boost"),
        "smt": _read(_TOPOLOGY / "smt" / "control"),
        "cpus": per_cpu,
    }
