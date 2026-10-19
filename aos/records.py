"""Run directories: configuration, provenance and raw per-slice data.

Raw data is the only exact record of a time-bounded run (a rerun with the
same seed drifts apart), so every run directory keeps it in full; summaries
for the journal are derived from it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from bench.metadata import host_metadata

RUNS_ROOT = Path(__file__).resolve().parents[1] / "runs"


def new_run_directory(kind: str, root: Path = RUNS_ROOT) -> Path:
    """Create and return ``root/kind/<timestamp>`` (a fresh directory).

    Raises:
        FileExistsError: If the directory already exists.
    """
    directory = root / kind / time.strftime("%Y%m%d-%H%M%S")
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def write_config(directory: Path, settings: dict[str, Any]) -> Path:
    """Write ``config.json`` with host provenance and the run settings."""
    path = directory / "config.json"
    payload = {"host": host_metadata(), "settings": settings}
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    return path


def write_arrays(path: Path, arrays: dict[str, np.ndarray]) -> Path:
    """Write named arrays to a compressed ``.npz`` file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **arrays)  # pyright: ignore[reportArgumentType]
    return path


def read_arrays(path: Path) -> dict[str, np.ndarray]:
    """Read all arrays of an ``.npz`` file into memory."""
    with np.load(path) as data:
        return {name: data[name] for name in data.files}
