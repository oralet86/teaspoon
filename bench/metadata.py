"""Host and environment metadata for reproducible benchmark runs.

The functions here collect best-effort provenance about the machine,
repository state and Python environment that produced a benchmark run.  A
missing or broken source of information (no git checkout, no
``/proc/cpuinfo``) yields ``None`` values instead of an error, because a
benchmark run should not fail just because metadata is unavailable.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import subprocess
import tomllib
from importlib import metadata as importlib_metadata
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPOSITORY_ROOT / "pyproject.toml"
LOCKFILE_PATH = REPOSITORY_ROOT / "uv.lock"

_THREAD_ENVIRONMENT_VARIABLES = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "PYTHONHASHSEED",
)

_REQUIREMENT_SPLIT = re.compile(r"[<>=!~;\s\[]")


def file_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_metadata(directory: Path = REPOSITORY_ROOT) -> dict[str, str | bool | None]:
    """Return the repository commit, branch and dirty state.

    Every field is ``None`` when ``directory`` is not a git checkout or the
    git command is unavailable, so callers can safely serialize the result.
    """

    def run(*arguments: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except OSError, subprocess.SubprocessError:
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()

    commit = run("rev-parse", "HEAD")
    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    status = run("status", "--porcelain")
    return {
        "commit": commit,
        "branch": branch,
        "dirty": None if status is None else bool(status),
    }


def lockfile_digests() -> dict[str, str | None]:
    """Return SHA-256 digests of the lock file and project configuration."""
    return {
        name: file_sha256(path) if path.is_file() else None
        for name, path in {
            "uv.lock": LOCKFILE_PATH,
            "pyproject.toml": PYPROJECT_PATH,
        }.items()
    }


def _requirement_name(requirement: str) -> str:
    """Return the bare distribution name of a PEP 508 requirement string."""
    return _REQUIREMENT_SPLIT.split(requirement.strip(), maxsplit=1)[0]


def package_versions() -> dict[str, str | None]:
    """Return the installed version of every dependency declared in pyproject.

    A declared dependency that is not installed maps to ``None``; an absent
    or unreadable ``pyproject.toml`` yields an empty mapping.
    """
    if not PYPROJECT_PATH.is_file():
        return {}
    document = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    dependencies = document.get("project", {}).get("dependencies", [])
    versions: dict[str, str | None] = {}
    for requirement in dependencies:
        name = _requirement_name(str(requirement))
        try:
            versions[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _cpu_model() -> str | None:
    """Return the human-readable CPU model, when the platform exposes it."""
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or None


def cpu_metadata() -> dict[str, str | int | None]:
    """Return the CPU model, architecture, and usable core counts."""
    affinity_count: int | None = None
    if hasattr(os, "sched_getaffinity"):
        affinity_count = len(os.sched_getaffinity(0))
    return {
        "model": _cpu_model(),
        "architecture": platform.machine(),
        "logical_count": os.cpu_count(),
        "affinity_count": affinity_count,
    }


def thread_environment() -> dict[str, str | None]:
    """Return thread-limiting environment variables as set for this run."""
    return {name: os.environ.get(name) for name in _THREAD_ENVIRONMENT_VARIABLES}
