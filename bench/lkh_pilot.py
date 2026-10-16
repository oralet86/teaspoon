"""Suite, arms and time budget of the LKH-3 operator-setting pilot.

The pilot asks one question: does the best fixed LKH-3 setting change from
instance to instance?  Each instance is solved under a small set of arms
(move type crossed with kick type) and several seeds.  The arms of one
(instance, seed) pair form a block that runs side by side, so they are
compared under the same machine load and the wall time of a suite is known
before it starts.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from instances import load_path
from solvers import read_parameter_file

from .suites import DATA_ROOT, load_bks

SUITE_DIRECTORY = Path(__file__).resolve().parent
_SUITE_FILES: Mapping[str, str] = {"pilot-1h": "lkh_pilot_1h.json"}


@dataclass(frozen=True, slots=True)
class Tier:
    """Time allowance of one instance size class.

    Attributes:
        budget_seconds: Search budget passed to LKH as ``TIME_LIMIT``.
        slack_seconds: Extra wall-clock seconds for preprocessing before the
            process is killed.
    """

    budget_seconds: int
    slack_seconds: int

    @property
    def deadline_seconds(self) -> int:
        """Wall-clock limit of one run, and therefore of one block."""
        return self.budget_seconds + self.slack_seconds


TIERS: Mapping[str, Tier] = {
    "mid": Tier(budget_seconds=20, slack_seconds=10),
    "large": Tier(budget_seconds=45, slack_seconds=20),
}


@dataclass(frozen=True, slots=True)
class Arm:
    """One fixed LKH setting under test.

    Attributes:
        name: Short identifier used in records and file names.
        move_type: ``MOVE_TYPE``, the size of the sequential basis move.
        kick_type: ``KICK_TYPE``; ``0`` selects LKH's own walk-based restart
            and ``K >= 4`` a random K-swap kick.
    """

    name: str
    move_type: int
    kick_type: int

    def overrides(self, base_parameters: Mapping[str, str]) -> dict[str, str | int]:
        """Return the parameter entries that switch a run to this arm.

        Constrained problem types are run with LKH's ``SPECIAL`` moves; an
        arm keeps that choice and only changes the move size.
        """
        move_type = (
            f"{self.move_type} SPECIAL"
            if uses_special_moves(base_parameters)
            else str(self.move_type)
        )
        return {"MOVE_TYPE": move_type, "KICK_TYPE": self.kick_type, "KICKS": 1}


# LKH accepts SPECIAL only with move types 3 and 5, which fixes the two
# levels of the move factor.  For the TSP the kick factor contrasts LKH's
# default walk with a double-bridge-sized random kick.
ARMS: tuple[Arm, ...] = (
    Arm("m5_walk", move_type=5, kick_type=0),
    Arm("m5_kick4", move_type=5, kick_type=4),
    Arm("m3_walk", move_type=3, kick_type=0),
    Arm("m3_kick4", move_type=3, kick_type=4),
)
# On constrained problem types the walk rarely reaches a feasible solution
# within the pilot's budgets, so it is not an open question there; the kick
# factor compares two kick sizes instead.
CONSTRAINED_ARMS: tuple[Arm, ...] = (
    Arm("m5_kick4", move_type=5, kick_type=4),
    Arm("m5_kick6", move_type=5, kick_type=6),
    Arm("m3_kick4", move_type=3, kick_type=4),
    Arm("m3_kick6", move_type=3, kick_type=6),
)
_UNCONSTRAINED_KINDS = frozenset({"TSP", "ATSP"})


def arms_for(kind: str) -> tuple[Arm, ...]:
    """Return the arms compared on instances of a problem type."""
    return ARMS if kind.upper() in _UNCONSTRAINED_KINDS else CONSTRAINED_ARMS


@dataclass(frozen=True, slots=True)
class PilotInstance:
    """One instance of a pilot suite with everything a run needs.

    Attributes:
        kind: Problem type, e.g. ``"CVRP"`` or ``"SOP"``.
        problem_path: Problem file.
        tier: Key into :data:`TIERS`.
        base_parameters: Problem-specific LKH settings shared by all arms.
        bks: Best-known cost, or ``None`` when unknown.
    """

    kind: str
    problem_path: Path
    tier: str
    base_parameters: Mapping[str, str]
    bks: float | None

    @property
    def name(self) -> str:
        """Instance name without directory or suffix."""
        return self.problem_path.stem


@dataclass(frozen=True, slots=True)
class PilotSuite:
    """A named list of instances and the seeds each is run with."""

    name: str
    seeds: tuple[int, ...]
    instances: tuple[PilotInstance, ...]


@dataclass(frozen=True, slots=True)
class PilotRun:
    """One planned LKH run: an instance under one arm and one seed."""

    instance: PilotInstance
    arm: Arm
    seed: int

    @property
    def label(self) -> str:
        """Unique name used for the run's trace and log files."""
        return f"{self.instance.name}__{self.arm.name}__seed{self.seed}"


Block = tuple[PilotRun, ...]
"""The runs of one (instance, seed) pair, executed side by side."""


def uses_special_moves(parameters: Mapping[str, str]) -> bool:
    """Return whether a parameter set selects LKH's ``SPECIAL`` moves."""
    upper = {key.upper(): value.upper() for key, value in parameters.items()}
    return "SPECIAL" in upper or "SPECIAL" in upper.get("MOVE_TYPE", "")


def load_suite(name_or_path: str | Path, *, data_root: Path = DATA_ROOT) -> PilotSuite:
    """Load a pilot suite from its JSON definition.

    Each instance entry has ``kind``, ``problem`` (relative to ``data_root``)
    and ``tier``, plus optionally ``parameters`` (an LKH parameter file
    relative to ``data_root``), ``base`` (inline LKH settings) and ``bks``.
    The best-known cost comes from ``bks``, else the parameter file's
    ``OPTIMUM``, else a CVRP companion solution.  A known integral best-known
    cost is passed to LKH as ``OPTIMUM`` so a run stops once it reaches it.

    Args:
        name_or_path: A built-in suite name such as ``"pilot-1h"`` or the
            path of a suite file.
        data_root: Root that instance and parameter paths are relative to.

    Raises:
        FileNotFoundError: If the suite file or any file it names is missing.
        ValueError: If the definition is malformed.
    """
    key = str(name_or_path)
    suite_path = SUITE_DIRECTORY / _SUITE_FILES[key] if key in _SUITE_FILES else None
    if suite_path is None:
        suite_path = Path(name_or_path)
    if not suite_path.is_file():
        known = ", ".join(_SUITE_FILES)
        raise FileNotFoundError(
            f"no pilot suite {key!r}; expected one of {known} or a suite file"
        )
    document = json.loads(suite_path.read_text(encoding="utf-8"))
    entries = document.get("instances")
    seeds = tuple(int(seed) for seed in document.get("seeds", ()))
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{suite_path} lists no instances")
    if not seeds or any(seed <= 0 for seed in seeds):
        # LKH replaces a zero seed with a time-based one.
        raise ValueError(f"{suite_path} needs positive seeds, got {seeds}")

    missing = [
        str(data_root / entry[field])
        for entry in entries
        for field in ("problem", "parameters")
        if field in entry and not (data_root / entry[field]).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"{suite_path.name} names {len(missing)} missing files, e.g. "
            f"{', '.join(missing[:3])}; fetch them with "
            "`python -m bench fetch-lkh` or the CVRPLIB/TSPLIB archives"
        )
    instances = tuple(_load_entry(entry, data_root, suite_path) for entry in entries)
    names = [instance.name for instance in instances]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"{suite_path} repeats instances: {', '.join(duplicates)}")
    return PilotSuite(
        name=str(document.get("name", suite_path.stem)),
        seeds=seeds,
        instances=instances,
    )


def _load_entry(
    entry: Mapping[str, object], data_root: Path, suite_path: Path
) -> PilotInstance:
    """Resolve one suite entry into a runnable instance.

    Raises:
        ValueError: If a required field is missing or the tier is unknown.
    """
    for field in ("kind", "problem", "tier"):
        if field not in entry:
            raise ValueError(f"{suite_path}: an instance entry has no {field!r}")
    kind = str(entry["kind"]).upper()
    tier = str(entry["tier"])
    if tier not in TIERS:
        raise ValueError(f"{suite_path}: unknown tier {tier!r}")
    problem_path = data_root / str(entry["problem"])

    base: dict[str, str] = {}
    bks: float | None = None
    if "parameters" in entry:
        base.update(read_parameter_file(data_root / str(entry["parameters"])))
        if base.get("OPTIMUM"):
            bks = float(base["OPTIMUM"])
    elif kind == "CVRP":
        base.update(_default_cvrp_parameters(problem_path))
        bks = load_bks(problem_path)
    inline = entry.get("base", {})
    if not isinstance(inline, dict):
        raise ValueError(f"{suite_path}: 'base' must be an object")
    base.update({str(key).upper(): str(value) for key, value in inline.items()})
    if entry.get("bks") is not None:
        bks = float(str(entry["bks"]))
    if bks is not None and bks == math.floor(bks):
        base["OPTIMUM"] = str(int(bks))
    else:
        base.pop("OPTIMUM", None)
    return PilotInstance(
        kind=kind,
        problem_path=problem_path,
        tier=tier,
        base_parameters=base,
        bks=bks,
    )


def _default_cvrp_parameters(problem_path: Path) -> dict[str, str]:
    """Return LKH settings for a CVRPLIB instance without a parameter file.

    The vehicle count is taken from the best-known solution when there is
    one, because the minimum implied by the demand is too small for some
    instances; without it LKH picks that minimum itself.
    """
    parameters = {"SPECIAL": ""}
    routes = len(load_path(problem_path).sequences)
    if routes:
        parameters["SALESMEN"] = str(routes)
    return parameters


def build_blocks(suite: PilotSuite) -> tuple[Block, ...]:
    """Return the suite's runs grouped into (instance, seed) blocks.

    Seeds form the outer loop, so an interrupted suite still holds complete
    replicates of every instance instead of every replicate of a few.

    Raises:
        ValueError: If a seed is repeated, which would make two blocks
            share their records and files.
    """
    if len(set(suite.seeds)) != len(suite.seeds):
        raise ValueError(f"seeds must be distinct, got {suite.seeds}")
    return tuple(
        tuple(
            PilotRun(instance=instance, arm=arm, seed=seed)
            for arm in arms_for(instance.kind)
        )
        for seed in suite.seeds
        for instance in suite.instances
    )


def build_waves(blocks: Sequence[Block], *, jobs: int) -> tuple[tuple[Block, ...], ...]:
    """Group consecutive blocks that run side by side.

    A wave holds as many whole blocks as fit into ``jobs`` processes, and
    only blocks of one tier, so every run of a wave has the same deadline.
    With fewer jobs than a block has arms, a wave is a single block whose
    runs queue for the available processes.

    Raises:
        ValueError: If ``jobs`` is not positive.
    """
    if jobs <= 0:
        raise ValueError(f"jobs must be positive, got {jobs}")
    waves: list[list[Block]] = []
    for block in blocks:
        if not block:
            continue
        current = waves[-1] if waves else None
        if (
            current is not None
            and current[0][0].instance.tier == block[0].instance.tier
            and sum(len(member) for member in current) + len(block) <= jobs
        ):
            current.append(block)
        else:
            waves.append([block])
    return tuple(tuple(wave) for wave in waves)


def worst_case_seconds(blocks: Sequence[Block], *, jobs: int) -> int:
    """Return an upper bound on the wall time of running ``blocks``.

    Every run is killed at its tier's deadline and a wave runs ``jobs``
    processes at a time, so the bound is exact up to process start-up.

    Raises:
        ValueError: If ``jobs`` is not positive.
    """
    return sum(
        math.ceil(sum(len(block) for block in wave) / jobs)
        * TIERS[wave[0][0].instance.tier].deadline_seconds
        for wave in build_waves(blocks, jobs=jobs)
    )
