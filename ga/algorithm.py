"""A steady-state genetic algorithm for the symmetric TSP.

Each step breeds one offspring: two parents are chosen by tournament, order
crossover combines them, the selector picks a mutation operator, and the
offspring replaces the worst tour of the population when it is better.  The
loop records which operator was used at every step and what it changed, so
a run can be analysed afterwards.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from .operators import MUTATIONS, Mutation, order_crossover, tour_length
from .selectors import OperatorSelector


@dataclass(frozen=True, slots=True)
class GaConfig:
    """Settings of one genetic algorithm run.

    Attributes:
        population_size: Number of tours kept.
        tournament_size: Tours compared when choosing a parent.
        crossover_rate: Probability that an offspring comes from crossover
            instead of being a copy of its first parent.
        max_evaluations: Offspring to breed before stopping.
        time_limit_seconds: Optional wall-clock limit, checked every step.
        seed: Seed of the random number generator.
    """

    population_size: int = 50
    tournament_size: int = 3
    crossover_rate: float = 0.9
    max_evaluations: int = 20_000
    time_limit_seconds: float | None = None
    seed: int = 0


@dataclass(frozen=True, slots=True)
class GaResult:
    """The outcome of one run.

    Attributes:
        tour: Best tour found, as node indices.
        length: Length of the best tour.
        evaluations: Offspring that were bred.
        runtime_seconds: Wall-clock time of the run.
        operator_names: The operators, in the order the log indexes them.
        log_operators: Operator index used at each evaluation.
        log_deltas: Length change the operator caused at each evaluation.
        log_best: Best length known after each evaluation.
    """

    tour: np.ndarray
    length: float
    evaluations: int
    runtime_seconds: float
    operator_names: tuple[str, ...]
    log_operators: np.ndarray
    log_deltas: np.ndarray
    log_best: np.ndarray


def improvement_reward(delta: float, length_before: float) -> float:
    """Return the relative improvement a mutation achieved, or zero.

    This is the signal a selector learns from.  It is its own function so
    that other credit definitions can be tried without touching the loop.
    """
    return max(0.0, -delta) / length_before


def run_ga(
    distances: np.ndarray,
    selector: OperatorSelector,
    config: GaConfig | None = None,
    mutations: Mapping[str, Mutation] = MUTATIONS,
) -> GaResult:
    """Run the genetic algorithm on a symmetric distance matrix.

    Args:
        distances: ``(n, n)`` symmetric matrix of distances.
        selector: Strategy that picks the mutation operator of each offspring.
        config: Run settings; the defaults are used when omitted.
        mutations: Operators the selector chooses between, by name.

    Returns:
        The best tour and the per-evaluation operator log.

    Raises:
        ValueError: If the matrix is not square and symmetric, has fewer
            than five nodes, or a setting is out of range.
        RuntimeError: If the incrementally tracked length of the best tour
            disagrees with its recomputed length, which would mean an
            operator reports a wrong length change.
    """
    config = config or GaConfig()
    _validate(distances, config)
    rng = np.random.default_rng(config.seed)
    names = tuple(mutations)
    operators = tuple(mutations.values())
    size = distances.shape[0]

    population = [rng.permutation(size) for _ in range(config.population_size)]
    lengths = np.array([tour_length(tour, distances) for tour in population])
    log_operators = np.empty(config.max_evaluations, dtype=np.int64)
    log_deltas = np.empty(config.max_evaluations)
    log_best = np.empty(config.max_evaluations)

    started = time.perf_counter()
    evaluations = 0
    for evaluation in range(config.max_evaluations):
        if (
            config.time_limit_seconds is not None
            and time.perf_counter() - started >= config.time_limit_seconds
        ):
            break
        first = _tournament(lengths, config.tournament_size, rng)
        if rng.random() < config.crossover_rate:
            second = _tournament(lengths, config.tournament_size, rng)
            child = order_crossover(population[first], population[second], rng)
            child_length = tour_length(child, distances)
        else:
            child = population[first].copy()
            child_length = float(lengths[first])

        operator = selector.select(rng)
        delta = operators[operator](child, distances, rng)
        selector.update(operator, improvement_reward(delta, child_length))
        child_length += delta

        worst = int(lengths.argmax())
        # Refusing a tour whose length is already present keeps the
        # population from filling up with copies of its best member.
        if child_length < lengths[worst] and not np.any(lengths == child_length):
            population[worst] = child
            lengths[worst] = child_length

        log_operators[evaluation] = operator
        log_deltas[evaluation] = delta
        log_best[evaluation] = lengths.min()
        evaluations = evaluation + 1

    best = int(lengths.argmin())
    recomputed = tour_length(population[best], distances)
    if not np.isclose(recomputed, lengths[best]):
        raise RuntimeError(
            f"tracked length {lengths[best]} differs from recomputed {recomputed}"
        )
    return GaResult(
        tour=population[best],
        length=recomputed,
        evaluations=evaluations,
        runtime_seconds=time.perf_counter() - started,
        operator_names=names,
        log_operators=log_operators[:evaluations],
        log_deltas=log_deltas[:evaluations],
        log_best=log_best[:evaluations],
    )


def _tournament(lengths: np.ndarray, size: int, rng: np.random.Generator) -> int:
    """Return the index of the shortest of ``size`` randomly drawn tours."""
    contenders = rng.choice(lengths.size, size=size, replace=False)
    return int(contenders[lengths[contenders].argmin()])


def _validate(distances: np.ndarray, config: GaConfig) -> None:
    """Check the distance matrix and the settings.

    Raises:
        ValueError: If any of them cannot be used.
    """
    if distances.ndim != 2 or distances.shape[0] != distances.shape[1]:
        raise ValueError(f"distances must be square, got shape {distances.shape}")
    if distances.shape[0] < 5:
        raise ValueError("the operators need at least five nodes")
    if not np.array_equal(distances, distances.T):
        raise ValueError("distances must be symmetric")
    if not 2 <= config.tournament_size <= config.population_size:
        raise ValueError(
            "tournament_size must be between 2 and population_size, got "
            f"{config.tournament_size} and {config.population_size}"
        )
    if not 0.0 <= config.crossover_rate <= 1.0:
        raise ValueError(
            f"crossover_rate must be in [0, 1], got {config.crossover_rate}"
        )
    if config.max_evaluations <= 0:
        raise ValueError(
            f"max_evaluations must be positive, got {config.max_evaluations}"
        )
