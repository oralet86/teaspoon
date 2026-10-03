"""Named CVRP instance suites, best-known-solution lookup and provenance."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from instances import load_path

from .metadata import REPOSITORY_ROOT, file_sha256

logger = logging.getLogger(__name__)

DATA_ROOT = REPOSITORY_ROOT / "data"
BKS_MANIFEST_PATH = REPOSITORY_ROOT / "bench" / "bks_manifest.json"
_MANIFEST_VERSION = 1

_SUITE_GLOBS: dict[str, str] = {
    "x": "X/*.vrp",
    "xl": "XL/*.vrp",
    "ags": "AGS/*.vrp",
}

# A few representative instances for plumbing checks: small, mid, large and
# real-world.
_SMOKE_INSTANCES: tuple[str, ...] = (
    "X/X-n101-k25.vrp",
    "X/X-n801-k40.vrp",
    "XL/XL-n1048-k237.vrp",
    "AGS/Leuven1.vrp",
)


@dataclass(eq=False, frozen=True, slots=True)
class BksProvenance:
    """Recorded provenance of an instance's companion best-known solution.

    Attributes:
        source: URL the solution was downloaded from, when recorded.
        sha256: SHA-256 digest of the companion file that was actually used.
        fetched: ISO date the solution was downloaded, when recorded.
    """

    source: str | None
    sha256: str | None
    fetched: str | None


def suite_instances(name: str, *, data_root: Path = DATA_ROOT) -> tuple[Path, ...]:
    """Return the instance paths of a named suite.

    Args:
        name: ``"x"``, ``"xl"``, ``"ags"`` or ``"smoke"``.
        data_root: Root that holds the ``X/``, ``XL/`` and ``AGS/`` folders.

    Raises:
        ValueError: If the suite name is unknown.
        FileNotFoundError: If a smoke-suite instance is missing.
    """
    if name == "smoke":
        paths = tuple(data_root / relative for relative in _SMOKE_INSTANCES)
        missing = [path for path in paths if not path.is_file()]
        if missing:
            names = ", ".join(str(path) for path in missing)
            raise FileNotFoundError(
                f"smoke suite instances are missing: {names}; "
                "download the CVRPLIB sets into data/ "
                "(https://galgos.inf.puc-rio.br/cvrplib/)"
            )
        return paths
    if name not in _SUITE_GLOBS:
        expected = ", ".join([*_SUITE_GLOBS, "smoke"])
        raise ValueError(f"unknown suite {name!r}; expected one of {expected}")
    return tuple(sorted(data_root.glob(_SUITE_GLOBS[name])))


def load_bks(instance_path: Path) -> float | None:
    """Return the best-known solution cost attached to an instance.

    The cost is recomputed from the companion ``.sol`` file with the
    instance's own length function, so it matches the CVRPLIB convention.

    Returns:
        The total cost, or ``None`` when the instance has no companion
        solution or the solution cannot be measured.
    """
    instance = load_path(instance_path)
    if not instance.sequences:
        return None
    lengths = [instance.compute_length(sequence) for sequence in instance.sequences]
    if any(length is None for length in lengths):
        return None
    return float(sum(length for length in lengths if length is not None))


def _companion_solution(instance_path: Path) -> Path | None:
    """Return the ``.sol`` companion of a CVRP instance, if it exists."""
    name = instance_path.name.removesuffix(".gz")
    stem = name.rpartition(".")[0] or name
    for suffix in (".sol", ".sol.gz"):
        candidate = instance_path.parent / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def load_manifest(path: Path | None = None) -> dict[str, dict[str, object]]:
    """Read a BKS manifest's entries, or an empty mapping when absent.

    Raises:
        ValueError: If the manifest exists but holds no ``entries`` mapping.
    """
    manifest_path = path if path is not None else BKS_MANIFEST_PATH
    if not manifest_path.is_file():
        return {}
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = document.get("entries")
    if not isinstance(entries, dict):
        raise ValueError(f"{manifest_path} has no 'entries' mapping")
    return entries


def save_manifest(
    entries: Mapping[str, Mapping[str, object]],
    path: Path | None = None,
) -> Path:
    """Write manifest entries sorted by file name and return the path."""
    manifest_path = path if path is not None else BKS_MANIFEST_PATH
    document = {
        "version": _MANIFEST_VERSION,
        "generated": time.strftime("%Y-%m-%d"),
        "entries": {name: dict(entries[name]) for name in sorted(entries)},
    }
    manifest_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def bks_provenance(
    instance_path: Path,
    *,
    manifest_path: Path | None = None,
) -> BksProvenance | None:
    """Return the recorded provenance of an instance's companion BKS.

    The returned digest is always computed from the file on disk, so it
    describes the solution actually used by a run.  A missing manifest entry
    or a digest that differs from the manifest is logged as a warning.

    Returns:
        The provenance, or ``None`` when the instance has no companion
        solution file.
    """
    companion = _companion_solution(instance_path)
    if companion is None:
        return None
    entries = load_manifest(manifest_path)
    entry = entries.get(companion.name)
    actual_digest = file_sha256(companion)
    if entry is None:
        logger.warning("no BKS manifest entry for %s", companion.name)
        return BksProvenance(source=None, sha256=actual_digest, fetched=None)
    recorded_digest = entry.get("sha256")
    if recorded_digest != actual_digest:
        logger.warning(
            "BKS digest mismatch for %s: manifest %s, on disk %s",
            companion.name,
            recorded_digest,
            actual_digest,
        )
    source = entry.get("source")
    fetched = entry.get("fetched")
    return BksProvenance(
        source=str(source) if source is not None else None,
        sha256=actual_digest,
        fetched=str(fetched) if fetched is not None else None,
    )
