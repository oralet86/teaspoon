"""Tests for the scalar operator-selection package ``aos``."""

import random

import numpy as np
import pytest

from aos import (
    OPERATORS,
    FixedArm,
    PopulationConfig,
    Problem,
    Tour,
    UniformArm,
    Work,
    descend,
    run_population,
    run_slice,
)
from aos.operators import improve_city
from aos.population import initial_population, replay


def random_problem(n: int, seed: int, integral: bool = True) -> Problem:
    """Return a random Euclidean instance with ``n`` cities."""
    rng = np.random.default_rng(seed)
    points = rng.uniform(0, 1000, size=(n, 2))
    matrix = np.sqrt(((points[:, None, :] - points[None, :, :]) ** 2).sum(axis=2))
    if integral:
        matrix = np.rint(matrix)
    return Problem.from_matrix(f"random{n}", matrix, neighbours=5, alpha_candidates=5)


def random_tour(problem: Problem, seed: int) -> Tour:
    """Return a random tour of ``problem``."""
    order = list(range(problem.n))
    random.Random(seed).shuffle(order)
    return Tour.from_order(order, problem)


@pytest.mark.parametrize("integral", [True, False])
@pytest.mark.parametrize("name", list(OPERATORS))
def test_operator_keeps_tour_consistent_and_never_lengthens(
    name: str, integral: bool
) -> None:
    operator = OPERATORS[name]
    rng = random.Random(1)
    for n in (8, 9, 13, 30, 60):
        problem = random_problem(n, seed=n, integral=integral)
        tour = random_tour(problem, seed=n)
        work = Work()
        for _ in range(2000 // n + 20):
            before = tour.length
            delta, anchor = operator(tour, problem, rng, work)
            assert delta <= 0
            assert tour.length == pytest.approx(before + delta)
            assert -1 <= anchor < n
            tour.check(problem)
        assert work.lookups > 0


def test_reverse_handles_wrapping_and_complements() -> None:
    problem = random_problem(11, seed=3)
    for start in range(11):
        for end in range(11):
            tour = Tour.from_order(list(range(11)), problem)
            tour.reverse(start, end)
            tour.length = problem.tour_length(tour.order)
            tour.check(problem)
            # The cycle equals reversing the forward stretch start..end.
            span = (end - start) % 11 + 1
            stretch = [(start + k) % 11 for k in range(span)]
            expected = list(range(11))
            for position, city in zip(stretch, reversed(stretch), strict=True):
                expected[position] = city
            assert _same_cycle(tour.order, expected)


def _same_cycle(first: list[int], second: list[int]) -> bool:
    """Return whether two orders describe the same undirected cycle."""
    n = len(first)
    edges_first = {frozenset((first[i - 1], first[i])) for i in range(n)}
    edges_second = {frozenset((second[i - 1], second[i])) for i in range(n)}
    return edges_first == edges_second


def test_neighbour_lists_match_brute_force() -> None:
    problem = random_problem(40, seed=5, integral=False)
    for city in range(problem.n):
        distances = sorted(
            (problem.dist[city][other], other)
            for other in range(problem.n)
            if other != city
        )
        assert problem.neighbours[city] == [other for _, other in distances[:5]]


def test_scans_find_nothing_at_a_full_two_opt_optimum() -> None:
    problem = random_problem(40, seed=7)
    tour = random_tour(problem, seed=7)
    work = Work()
    # Run full-scan 2-opt to a true local optimum over all anchors.
    improved = True
    while improved:
        improved = False
        for anchor in range(problem.n):
            rng = _FixedChoice(anchor)
            delta, _ = OPERATORS["two_opt_best"](tour, problem, rng, work)  # type: ignore[arg-type]
            improved = improved or delta < 0
    tour.check(problem)
    for anchor in range(problem.n):
        rng = _FixedChoice(anchor)
        for name in ("two_opt_first", "two_opt_best"):
            delta, _ = OPERATORS[name](tour, problem, rng, work)  # type: ignore[arg-type]
            assert delta == 0
    for city in range(problem.n):
        delta, touched = improve_city(tour, problem, city, work)
        assert delta == 0 and touched == ()


class _FixedChoice:
    """Stand-in for ``random.Random`` whose ``randrange`` returns a fixed value."""

    def __init__(self, value: int) -> None:
        self._value = value

    def randrange(self, stop: int) -> int:
        return self._value % stop


def test_descend_reaches_a_neighbour_list_optimum() -> None:
    problem = random_problem(80, seed=9)
    tour = random_tour(problem, seed=9)
    start = tour.length
    total = descend(tour, problem, Work())
    tour.check(problem)
    assert tour.length == pytest.approx(start + total)
    assert total < 0
    for city in range(problem.n):
        assert improve_city(tour, problem, city, Work())[0] == 0


def test_slice_count_mode_is_deterministic() -> None:
    problem = random_problem(50, seed=11)
    results = []
    for _ in range(2):
        tour = random_tour(problem, seed=11)
        record = run_slice(
            OPERATORS["inversion"],
            tour,
            problem,
            random.Random(4),
            applications=300,
        )
        results.append((tour.order, record.gain, record.improving, record.lookups))
    assert results[0] == results[1]
    assert results[0][1] > 0


def test_slice_time_mode_stops_after_the_deadline() -> None:
    problem = random_problem(50, seed=12)
    tour = random_tour(problem, seed=12)
    record = run_slice(
        OPERATORS["swap"], tour, problem, random.Random(0), seconds=0.002
    )
    assert record.duration_ns >= 2_000_000
    assert record.overshoot_ns >= 0
    assert record.applications >= 1
    assert record.gain == pytest.approx(
        problem.tour_length(random_tour(problem, 12).order) - tour.length
    )


def test_slice_rejects_bad_limits() -> None:
    problem = random_problem(20, seed=13)
    tour = random_tour(problem, seed=13)
    with pytest.raises(ValueError):
        run_slice(OPERATORS["swap"], tour, problem, random.Random(0))
    with pytest.raises(ValueError):
        run_slice(
            OPERATORS["swap"],
            tour,
            problem,
            random.Random(0),
            seconds=1.0,
            applications=1,
        )


def test_initial_population_depends_only_on_the_seed() -> None:
    problem = random_problem(30, seed=14)
    first = [tour.order for tour in initial_population(problem, 5, seed=3)]
    second = [tour.order for tour in initial_population(problem, 5, seed=3)]
    other = [tour.order for tour in initial_population(problem, 5, seed=4)]
    assert first == second
    assert first != other


def test_population_improves_and_replay_is_exact() -> None:
    problem = random_problem(60, seed=15)
    config = PopulationConfig(
        size=6, tournament=2, slice_seconds=0.0005, budget_slices=200, seed=2
    )
    result = run_population(problem, UniformArm(), config)
    log = result.log
    assert len(log) == 200
    result.best.check(problem)
    best_lengths = np.asarray(log.best_length)
    assert np.all(np.diff(best_lengths) <= 1e-9)
    assert best_lengths[-1] < max(log.length_before[:6])
    replayed = replay(problem, log, config)
    assert replayed.best.order == result.best.order
    assert replayed.log.length_after == log.length_after
    assert replayed.log.accepted == log.accepted


def test_population_replaces_only_with_shorter_new_lengths() -> None:
    problem = random_problem(40, seed=16)
    config = PopulationConfig(
        size=5, tournament=2, slice_applications=20, budget_slices=100, seed=1
    )
    result = run_population(problem, FixedArm("two_opt_nl"), config)
    log = result.log
    for index in range(len(log)):
        if log.accepted[index]:
            # An unchanged copy repeats its parent's length and is refused.
            assert log.gain[index] > 0
            assert log.length_after[index] < log.length_before[index]
    assert set(log.operator) == {list(OPERATORS).index("two_opt_nl")}


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        PopulationConfig(budget_slices=1).validate()
    with pytest.raises(ValueError):
        PopulationConfig(slice_applications=1).validate()
    with pytest.raises(ValueError):
        FixedArm("unknown")


def test_uniform_arm_allow_adds_an_operator_once() -> None:
    arm = UniformArm(["swap"])
    rng = random.Random(0)
    assert {arm.next(rng)[0] for _ in range(50)} == {0}
    arm.allow("kick")
    arm.allow("kick")
    chosen = {arm.next(rng)[0] for _ in range(200)}
    assert chosen == {0, list(OPERATORS).index("kick")}
    with pytest.raises(ValueError):
        arm.allow("unknown")
