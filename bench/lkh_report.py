"""Go/no-go summary of an LKH-3 setting pilot.

The question is whether the best arm changes between instances.  An instance
only counts towards the answer when it can separate the arms: every run must
have ended with a feasible solution, and the instance must not be solved to
best-known by every run.  A win is "clear" when the winning arm's worst seed
still beats every other arm's best seed, so seed noise cannot explain it;
with a single seed per arm no win is clear.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .metrics import mean_or_nan

Score = tuple[float, float]
"""``(penalty, cost)``, compared in that order as LKH does."""

_USABLE_STATUSES = ("ok", "killed")


@dataclass(frozen=True, slots=True)
class InstanceOutcome:
    """How the arms compare on one instance.

    Attributes:
        instance: Instance name.
        kind: Problem type.
        scores: Per arm, the ``(penalty, cost)`` of every seed.
        arms: Every arm planned for the instance, including failed ones.
        expected_runs: Runs planned for the instance, including failed ones.
        feasible_runs: Runs that ended with penalty zero.
        invalid_runs: Runs whose tour failed the Python-side check although
            LKH reported it feasible.
        saturated: Every run reached the best-known cost.
        winner: Arm with the best mean score; ``None`` when several arms
            share it.
        clear: Whether the winner's worst seed beats all other arms' best.
        mean_gaps: Per arm, the mean gap to best-known over feasible runs.
    """

    instance: str
    kind: str
    scores: Mapping[str, tuple[Score, ...]]
    arms: tuple[str, ...]
    expected_runs: int
    feasible_runs: int
    invalid_runs: int
    saturated: bool
    winner: str | None
    clear: bool
    mean_gaps: Mapping[str, float]

    @property
    def usable_runs(self) -> int:
        """Runs that reported a solution."""
        return sum(len(values) for values in self.scores.values())

    @property
    def state(self) -> str:
        """Why the instance does or does not count towards the verdict."""
        if self.usable_runs < self.expected_runs:
            return "incomplete"
        if self.invalid_runs:
            return "invalid"
        if self.feasible_runs < self.usable_runs:
            return "infeasible"
        if self.saturated:
            return "saturated"
        if self.winner is None:
            return "tied"
        return "decided"


def instance_outcomes(records: Sequence[Mapping[str, Any]]) -> list[InstanceOutcome]:
    """Group run records by instance and compare the arms on each."""
    names = sorted({str(record["instance"]) for record in records})
    outcomes: list[InstanceOutcome] = []
    for name in names:
        group = [record for record in records if record["instance"] == name]
        usable = [record for record in group if record["status"] in _USABLE_STATUSES]
        arms = sorted({str(record["arm"]) for record in usable})
        scores = {
            arm: tuple(
                (float(record["penalty"]), float(record["cost"]))
                for record in usable
                if record["arm"] == arm
            )
            for arm in arms
        }
        gaps = {
            arm: mean_or_nan(
                [
                    float(record["gap"])
                    for record in usable
                    if record["arm"] == arm and record["gap"] is not None
                ]
            )
            for arm in arms
        }
        winner, clear = _winner(scores)
        outcomes.append(
            InstanceOutcome(
                instance=name,
                kind=str(group[0]["kind"]),
                scores=scores,
                arms=tuple(sorted({str(record["arm"]) for record in group})),
                expected_runs=len(group),
                feasible_runs=sum(record["penalty"] == 0 for record in usable),
                invalid_runs=sum(_is_invalid(record) for record in usable),
                saturated=bool(usable)
                and all(record["reached_bks"] is True for record in usable),
                winner=winner,
                clear=clear,
                mean_gaps=gaps,
            )
        )
    return outcomes


def _is_invalid(record: Mapping[str, Any]) -> bool:
    """Return whether a feasible-by-LKH run failed the Python-side check.

    A killed run has no final tour to check, and a problem type without a
    Python-side check is not held against the run.
    """
    if record["penalty"] != 0 or record["status"] != "ok":
        return False
    if record.get("validation_error") is None:
        validated = record.get("validated_cost")
        return validated is not None and float(validated) != float(record["cost"])
    return not str(record["validation_error"]).startswith("no Python-side check")


def _winner(scores: Mapping[str, tuple[Score, ...]]) -> tuple[str | None, bool]:
    """Return the arm with the best mean score and whether its win is clear."""
    filled = {arm: values for arm, values in scores.items() if values}
    if len(filled) < 2:
        return None, False
    means = {
        arm: (
            mean_or_nan([penalty for penalty, _ in values]),
            mean_or_nan([cost for _, cost in values]),
        )
        for arm, values in filled.items()
    }
    winner = min(means, key=lambda arm: means[arm])
    if sum(score == means[winner] for score in means.values()) > 1:
        return None, False
    worst_of_winner = max(filled[winner])
    replicated = all(len(values) >= 2 for values in filled.values())
    clear = replicated and all(
        worst_of_winner < min(values) for arm, values in filled.items() if arm != winner
    )
    return winner, clear


def verdict(outcomes: Sequence[InstanceOutcome]) -> str:
    """Return the go/no-go reading, one line per problem type.

    Each type is judged apart.  A winner that only changes between types is
    no case for adapting during a run, because a fixed setting chosen per
    type captures it; that situation gets its own closing line instead of a
    GO.
    """
    groups: dict[str, list[InstanceOutcome]] = {}
    for outcome in outcomes:
        groups.setdefault(outcome.kind, []).append(outcome)
    if len(groups) <= 1:
        return _group_verdict(outcomes)
    lines = [f"{kind}: {_group_verdict(groups[kind])}" for kind in sorted(groups)]
    # Leaders are only comparable between types that ran the same arms.
    leaders: dict[tuple[str, ...], dict[str, str]] = {}
    for kind in sorted(groups):
        arm = _leading_arm(groups[kind])
        if arm is not None:
            leaders.setdefault(groups[kind][0].arms, {})[kind] = arm
    for named in leaders.values():
        if len(set(named.values())) > 1:
            detail = ", ".join(f"{kind} {arm}" for kind, arm in named.items())
            lines.append(
                f"Between types the most frequent winner differs ({detail}); "
                "a fixed setting per type covers that."
            )
    return "\n".join(lines)


def _leading_arm(outcomes: Sequence[InstanceOutcome]) -> str | None:
    """Return the arm that wins most decided instances, if one leads alone."""
    wins = Counter(
        outcome.winner for outcome in outcomes if outcome.state == "decided"
    ).most_common(2)
    if not wins or (len(wins) == 2 and wins[0][1] == wins[1][1]):
        return None
    return wins[0][0]


def _group_verdict(outcomes: Sequence[InstanceOutcome]) -> str:
    """Return the reading of the instances of one problem type."""
    decided = [outcome for outcome in outcomes if outcome.state == "decided"]
    if not decided:
        return "NO VERDICT: no instance separates the arms with feasible solutions."
    clear_wins = Counter(outcome.winner for outcome in decided if outcome.clear)
    nominal_wins = Counter(outcome.winner for outcome in decided)
    if len(clear_wins) >= 2:
        detail = ", ".join(f"{arm} {count}" for arm, count in clear_wins.most_common())
        return (
            f"GO: the clear winner changes between instances ({detail} "
            f"of {len(decided)} decided)."
        )
    if len(clear_wins) == 1:
        arm, count = next(iter(clear_wins.items()))
        if nominal_wins[arm] == len(decided):
            return f"NO-GO: {arm} wins on all {len(decided)} decided instances."
        return (
            f"WEAK: only {arm} wins clearly ({count} of {len(decided)} decided); "
            "the other differences are within seed noise."
        )
    return (
        f"INCONCLUSIVE: no clear win on {len(decided)} decided instances; "
        "the differences are within seed noise."
    )


def summarize_pilot(records: Sequence[Mapping[str, Any]]) -> str:
    """Return the pilot report: feasibility, per-type wins and the verdict."""
    outcomes = instance_outcomes(records)
    arms = sorted({str(record["arm"]) for record in records})
    kinds = sorted({outcome.kind for outcome in outcomes})
    lines = [_feasibility_table(records, kinds), ""]

    header = f"{'type':<8} {'decided':>8} " + " ".join(f"{arm:>16}" for arm in arms)
    lines += ["Wins per arm (clear wins in brackets)", header, "-" * len(header)]
    for kind in kinds:
        decided = [
            outcome
            for outcome in outcomes
            if outcome.kind == kind and outcome.state == "decided"
        ]
        cells = [
            f"{sum(o.winner == arm for o in decided)} "
            f"[{sum(o.winner == arm and o.clear for o in decided)}]"
            for arm in arms
        ]
        lines.append(
            f"{kind:<8} {len(decided):>8} " + " ".join(f"{cell:>16}" for cell in cells)
        )

    header = f"{'instance':<22} {'type':<7} {'state':<11} {'winner':<10} " + " ".join(
        f"{arm:>10}" for arm in arms
    )
    lines += ["", "Mean gap to best-known in % (feasible runs)", header]
    lines.append("-" * len(header))
    for outcome in outcomes:
        winner = "-" if outcome.winner is None else outcome.winner
        if outcome.clear:
            winner += "*"
        gaps = " ".join(
            f"{outcome.mean_gaps.get(arm, float('nan')) * 100:>10.3f}" for arm in arms
        )
        lines.append(
            f"{outcome.instance:<22} {outcome.kind:<7} {outcome.state:<11} "
            f"{winner:<10} {gaps}"
        )
    lines += ["* clear win: worst seed beats every other arm's best seed", ""]
    lines.append(verdict(outcomes))
    return "\n".join(lines)


def _feasibility_table(
    records: Sequence[Mapping[str, Any]], kinds: Sequence[str]
) -> str:
    """Return per-type counts of failed, infeasible and invalid runs."""
    header = (
        f"{'type':<8} {'runs':>5} {'errors':>7} {'killed':>7} {'feasible':>9} "
        f"{'checked ok':>11} {'check failed':>13} {'at BKS':>7}"
    )
    lines = ["Feasibility", header, "-" * len(header)]
    for kind in kinds:
        group = [record for record in records if record["kind"] == kind]
        usable = [record for record in group if record["status"] in _USABLE_STATUSES]
        checked = sum(
            record.get("validated_cost") is not None and not _is_invalid(record)
            for record in usable
        )
        lines.append(
            f"{kind:<8} {len(group):>5} {len(group) - len(usable):>7} "
            f"{sum(record['status'] == 'killed' for record in group):>7} "
            f"{sum(record['penalty'] == 0 for record in usable):>9} "
            f"{checked:>11} {sum(_is_invalid(record) for record in usable):>13} "
            f"{sum(record['reached_bks'] is True for record in usable):>7}"
        )
    return "\n".join(lines)
