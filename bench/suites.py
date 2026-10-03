"""Named CVRP instance suites and best-known-solution lookup."""

from __future__ import annotations

from pathlib import Path

from instances import load_path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPOSITORY_ROOT / "data"

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
