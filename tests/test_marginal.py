"""Tests for anchored operators and E1 measurement A (marginal rates)."""

import csv
import random
from pathlib import Path

import numpy as np
import pytest

from aos import OPERATORS, Problem, Tour, Work, descend
from aos.marginal import (
    MarginalSettings,
    ReplayError,
    TimingSettings,
    measure_gains,
    measure_marginal,
    measure_times,
    precise,
    summarise_passes,
)
from aos.operators import DETERMINISTIC


def random_problem(n: int, seed: int) -> Problem:
    """Return a random integral Euclidean instance with ``n`` cities."""
    rng = np.random.default_rng(seed)
    points = rng.uniform(0, 1000, size=(n, 2))
    matrix = np.rint(
        np.sqrt(((points[:, None, :] - points[None, :, :]) ** 2).sum(axis=2))
    )
    return Problem.from_matrix(f"random{n}", matrix, neighbours=6, alpha_candidates=5)


def random_tour(problem: Problem, seed: int) -> Tour:
    """Return a random tour of ``problem``."""
    order = list(range(problem.n))
    random.Random(seed).shuffle(order)
    return Tour.from_order(order, problem)


def states(problem: Problem) -> list[Tour]:
    """A random tour and an O7 local optimum: early and late states."""
    early = random_tour(problem, seed=1)
    late = early.copy()
    descend(late, problem, Work())
    return [early, late]


@pytest.mark.parametrize("name", sorted(DETERMINISTIC))
def test_deterministic_operators_depend_only_on_the_anchor(name: str) -> None:
    problem = random_problem(40, seed=2)
    for state in states(problem):
        for anchor in range(problem.n):
            results = []
            for seed in (0, 1):
                tour = state.copy()
                delta, _ = OPERATORS[name](
                    tour, problem, random.Random(seed), Work(), anchor
                )
                results.append((delta, tour.order))
            assert results[0] == results[1]


@pytest.mark.parametrize("name", list(OPERATORS))
def test_anchor_is_used_and_failures_leave_the_tour_unchanged(name: str) -> None:
    problem = random_problem(30, seed=3)
    rng = random.Random(4)
    for state in states(problem):
        for anchor in range(problem.n):
            tour = state.copy()
            delta, used = OPERATORS[name](tour, problem, rng, Work(), anchor)
            assert used == anchor
            tour.check(problem)
            if delta == 0:
                assert tour.order == state.order
                assert tour.pos == state.pos
                assert tour.length == state.length


def test_enumerating_anchors_matches_random_anchors() -> None:
    problem = random_problem(50, seed=5)
    state = random_tour(problem, seed=6)
    operator = OPERATORS["two_opt_nl"]
    exact = []
    for anchor in range(problem.n):
        tour = state.copy()
        exact.append(-operator(tour, problem, random.Random(0), Work(), anchor)[0])
    rng = random.Random(7)
    sampled = []
    for _ in range(20_000):
        tour = state.copy()
        sampled.append(-operator(tour, problem, rng, Work())[0])
    error = np.std(sampled) / np.sqrt(len(sampled))
    assert abs(np.mean(sampled) - np.mean(exact)) < 4 * error


def test_measure_marginal_counts_every_anchor_and_keeps_the_state() -> None:
    problem = random_problem(20, seed=8)
    state = random_tour(problem, seed=9)
    before = (state.order[:], state.pos[:], state.length)
    names = list(OPERATORS)
    arrays = measure_marginal(
        problem, state, names, MarginalSettings(repeats=3, seed=0), 0, "test"
    )
    assert (state.order, state.pos, state.length) == before
    for name in names:
        anchors = arrays[name]["anchor"]
        assert len(anchors) == 3 * problem.n
        assert np.all(np.bincount(anchors, minlength=problem.n) == 3)
        assert np.all(arrays[name]["gain"] >= 0)
    # A deterministic operator gains the same at every repeat of an anchor.
    gains = arrays["two_opt_nl"]
    for anchor in range(problem.n):
        assert len(set(gains["gain"][gains["anchor"] == anchor])) == 1


def test_summarise_passes_rate_is_mean_gain_over_mean_time() -> None:
    anchors = np.asarray([0, 0, 1, 1])
    repeats = np.asarray([0, 1, 0, 1])
    gains = {
        "anchor": anchors,
        "repeat": repeats,
        "gain": np.asarray([0.0, 4.0, 2.0, 0.0]),
    }
    times = {
        "anchor": anchors,
        "round": repeats,
        "time_ns": np.asarray([1000, 3000, 2000, 2000]),
    }
    summary = summarise_passes(gains, times, 2, 100, np.random.default_rng(0))
    assert summary.rate == pytest.approx(6.0 / 8e-6)
    assert summary.p_improve == 0.5
    assert summary.improving_anchors == 2
    assert summary.low <= summary.rate <= summary.high


def test_two_opt_descent_starts_with_o7_and_only_adds_gain() -> None:
    problem = random_problem(40, seed=13)
    state = random_tour(problem, seed=14)
    for anchor in range(problem.n):
        descent = state.copy()
        single = state.copy()
        delta_descent, _ = OPERATORS["two_opt_descent"](
            descent, problem, random.Random(0), Work(), anchor
        )
        delta_single, _ = OPERATORS["two_opt_nl"](
            single, problem, random.Random(0), Work(), anchor
        )
        descent.check(problem)
        assert delta_descent <= delta_single
        assert (delta_descent < 0) == (delta_single < 0)


def test_two_opt_targeted_applies_o7_at_the_worst_city() -> None:
    problem = random_problem(40, seed=15)
    state = random_tour(problem, seed=16)
    order, pos, dist = state.order, state.pos, problem.dist
    n = problem.n

    def score(city: int) -> float:
        place = pos[city]
        longest = max(dist[city][order[(place + 1) % n]], dist[city][order[place - 1]])
        return longest - dist[city][problem.neighbours[city][0]]

    worst = max(range(n), key=score)
    targeted = state.copy()
    plain = state.copy()
    delta_targeted, _ = OPERATORS["two_opt_targeted"](
        targeted, problem, random.Random(0), Work(), worst
    )
    delta_plain, _ = OPERATORS["two_opt_nl"](
        plain, problem, random.Random(0), Work(), worst
    )
    assert delta_targeted == delta_plain
    assert targeted.order == plain.order


def test_alpha_candidates_cover_a_minimum_spanning_tree() -> None:
    """Every MST edge has alpha 0, so with 5 candidates each tree edge is a
    candidate of at least one endpoint (small instance, few ties)."""
    from aos.candidates import alpha_candidates, minimum_one_tree

    problem = random_problem(30, seed=17)
    weights = np.asarray(problem.dist, dtype=float)
    lists = alpha_candidates(weights, np.zeros(problem.n), 5)
    parent, _order, _total, _degree = minimum_one_tree(weights)
    for city, up in enumerate(parent.tolist()):
        if up >= 0:
            assert up in lists[city] or city in lists[up]


def test_alpha_operators_need_candidates() -> None:
    plain = Problem.from_matrix("plain", np.asarray(random_problem(10, 1).dist))
    tour = random_tour(plain, 0)
    with pytest.raises(ValueError):
        OPERATORS["two_opt_alpha"](tour, plain, random.Random(0), Work(), 0)


def test_pass_one_runs_deterministic_operators_once_per_anchor() -> None:
    problem = random_problem(20, seed=20)
    state = random_tour(problem, seed=21)
    gains = measure_gains(
        problem, state, list(OPERATORS), MarginalSettings(repeats=3, seed=0), "test"
    )
    for name, arrays in gains.items():
        expected = 1 if name in DETERMINISTIC else 3
        assert np.all(np.bincount(arrays["anchor"], minlength=problem.n) == expected)
        improving = arrays["gain"] > 0
        assert np.all((arrays["tour_crc"] != 0) == improving)


def test_replay_reproduces_pass_one_in_every_round() -> None:
    problem = random_problem(25, seed=22)
    settings = MarginalSettings(repeats=2, seed=0)
    for index, state in enumerate(states(problem)):
        label = f"test:{index}"
        gains = measure_gains(problem, state, list(OPERATORS), settings, label)
        before = (state.order[:], state.length)
        times, reason = measure_times(
            problem,
            state,
            gains,
            settings,
            TimingSettings(min_rounds=2, max_rounds=3, precision=0.0),
            0,
            label,
        )
        assert (state.order, state.length) == before
        assert reason == "max_rounds"
        for name, arrays in times.items():
            assert len(arrays["time_ns"]) == 3 * problem.n
            assert set(arrays["round"].tolist()) == {0, 1, 2}
            if name not in DETERMINISTIC:
                # Round r replays repeat r mod 2.
                assert np.array_equal(arrays["repeat"], arrays["round"] % 2)


@pytest.mark.parametrize("column", ["gain", "tour_crc"])
def test_a_replay_that_differs_from_pass_one_raises(column: str) -> None:
    problem = random_problem(25, seed=23)
    state = random_tour(problem, seed=24)
    settings = MarginalSettings(repeats=1, seed=0)
    gains = measure_gains(problem, state, ["two_opt_nl"], settings, "test")
    arrays = gains["two_opt_nl"]
    improving = int(np.flatnonzero(arrays["gain"] > 0)[0])
    arrays[column][improving] += 1
    with pytest.raises(ReplayError):
        measure_times(problem, state, gains, settings, TimingSettings(), 0, "test")


def test_stop_rule_respects_min_and_max_rounds() -> None:
    problem = random_problem(20, seed=25)
    state = random_tour(problem, seed=26)
    settings = MarginalSettings(repeats=1, seed=0)
    gains = measure_gains(problem, state, ["two_opt_nl", "swap"], settings, "test")
    loose = TimingSettings(min_rounds=3, max_rounds=6, precision=100.0)
    times, reason = measure_times(problem, state, gains, settings, loose, 0, "test")
    assert reason == "precision"
    assert int(times["swap"]["round"].max()) == 2
    strict = TimingSettings(min_rounds=2, max_rounds=4, precision=0.0)
    times, reason = measure_times(problem, state, gains, settings, strict, 0, "test")
    assert reason == "max_rounds"
    assert int(times["swap"]["round"].max()) == 3
    with pytest.raises(ValueError):
        measure_times(
            problem, state, gains, settings, TimingSettings(min_rounds=1), 0, "test"
        )


def test_precise_needs_two_rounds_and_a_tight_interval() -> None:
    assert not precise([100.0], 0.5)
    assert precise([100.0, 100.0], 0.01)
    assert not precise([100.0, 120.0], 0.02)


def test_summarise_passes_rate_and_intervals_on_sampled_and_all_anchors() -> None:
    rng = np.random.default_rng(27)
    anchors = np.repeat(np.arange(6), 4)
    repeats = np.tile(np.arange(4), 6)
    gain = rng.exponential(3.0, size=24) * (rng.random(24) < 0.5)
    time_ns = rng.integers(1000, 5000, size=24)
    gains = {"anchor": anchors, "repeat": repeats, "gain": gain}
    times = {"anchor": anchors, "round": repeats, "time_ns": time_ns}
    expected = gain.sum() / (time_ns.sum() / 1e9)
    sampled = summarise_passes(gains, times, 10, 200, np.random.default_rng(0), True)
    assert sampled.rate == pytest.approx(expected)
    assert sampled.low <= sampled.rate <= sampled.high
    assert sampled.p_improve == pytest.approx(np.mean(gain > 0))
    # Every city an anchor: the stratified bootstrap gives the same rate.
    stratified = summarise_passes(gains, times, 6, 200, np.random.default_rng(0))
    assert stratified.rate == pytest.approx(expected)
    assert stratified.low <= stratified.rate <= stratified.high


def test_summarise_passes_rejects_different_anchors() -> None:
    gains = {
        "anchor": np.asarray([0, 1]),
        "repeat": np.asarray([0, 0]),
        "gain": np.asarray([1.0, 0.0]),
    }
    times = {
        "anchor": np.asarray([0, 2]),
        "round": np.asarray([0, 0]),
        "time_ns": np.asarray([10, 10]),
    }
    with pytest.raises(ValueError):
        summarise_passes(gains, times, 3, 10, np.random.default_rng(0))


def test_e1_marginal_stages_write_cells_and_resummarise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aos import e1_marginal

    problem = random_problem(20, seed=28)
    monkeypatch.setattr(
        e1_marginal.Problem, "from_tsplib", classmethod(lambda cls, path: problem)
    )
    task = e1_marginal.StateTask(
        path="unused.tsp",
        instance="random20",
        seed=0,
        state={"name": "early", "order": random_tour(problem, 29).order, "gap": None},
    )
    settings = e1_marginal.Settings(
        repeats=2,
        seed=0,
        bootstrap=50,
        instances=(),
        seeds=(),
        stages=(),
        min_rounds=2,
        max_rounds=2,
    )
    assert e1_marginal.gain_state((task, settings, tmp_path)) == task.label
    assert (tmp_path / "gains" / "random20" / "s0" / "early.npz").is_file()
    rows = e1_marginal.time_state(task, settings, tmp_path)
    assert {row["operator"] for row in rows} == set(OPERATORS)
    for row in rows:
        assert row["timed_rounds"] == 2
        assert row["timed_applications"] == 2 * problem.n
        assert row["gain_applications"] == row["gain_repeats"] * problem.n
    with (tmp_path / "cells.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    e1_marginal.resummarise(tmp_path, settings.bootstrap, settings.seed)
    with (tmp_path / "cells.csv").open(newline="") as handle:
        again = list(csv.DictReader(handle))
    for before, after in zip(rows, again, strict=True):
        assert float(after["rate"]) == pytest.approx(before["rate"])
