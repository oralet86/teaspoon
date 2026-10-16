"""Tests for the reported-result LKH runner and the setting pilot.

A fake LKH script is driven through the real subprocess plumbing, so no
vendored binary or downloaded benchmark is needed.
"""

from __future__ import annotations

import io
import json
import os
import signal
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest

from bench.__main__ import main
from bench.fetch_lkh_benchmarks import fetch_lkh_benchmarks
from bench.lkh_pilot import (
    ARMS,
    CONSTRAINED_ARMS,
    TIERS,
    PilotSuite,
    build_blocks,
    build_waves,
    load_suite,
    worst_case_seconds,
)
from bench.lkh_report import instance_outcomes, summarize_pilot, verdict
from bench.lkh_runner import (
    STOP_AFTER_BLOCK,
    STOP_NOW,
    archive_run,
    pilot_progress,
    run_pilot,
)
from bench.report import load_records
from solvers import SolverExecutionError, read_parameter_file, run_lkh_reported

# Prints an infeasible then a feasible improvement and writes the identity
# tour.  FAKE_COST fixes the cost; otherwise move type 3 is worse than 5.
_FAKE_LKH_SOURCE = r"""
import sys
from pathlib import Path

parameter_path = Path(sys.argv[1])
parameters = {}
for line in parameter_path.read_text().splitlines():
    key, _, value = line.partition("=")
    parameters[key.strip()] = value.strip()
(Path(__file__).parent / "lkh.parameters.saved").write_text(parameter_path.read_text())
dimension = 0
for line in Path(parameters["PROBLEM_FILE"]).read_text().splitlines():
    if line.upper().startswith("DIMENSION"):
        dimension = int(line.split(":")[1])
if "FAKE_COST" in parameters:
    cost = int(parameters["FAKE_COST"])
else:
    cost = 100 if parameters.get("MOVE_TYPE", "5").startswith("3") else 90
print("* 1: Cost = 7_200, Time = 0.01 sec.", flush=True)
if "FAKE_PLAIN" in parameters:
    print(f"* 2: Cost = {cost}, Gap = 0.0000%, Time = 0.02 sec. =", flush=True)
else:
    print(f"* 2: Cost = 0_{cost}, Time = 0.02 sec.", flush=True)
tour = ["TYPE : TOUR", f"DIMENSION : {dimension}", "TOUR_SECTION"]
tour += [str(node) for node in range(1, dimension + 1)] + ["-1", "EOF"]
Path(parameters["TOUR_FILE"]).write_text("\n".join(tour) + "\n")
print("Trials.min = 5, Trials.avg = 5.0, Trials.max = 5")
"""

_STALLING_LKH_SOURCE = r"""
import time
print("* 1: Cost = 3_500, Time = 0.01 sec.", flush=True)
time.sleep(30)
"""

# Logs the problem it was asked to solve, then takes a moment to finish.
_SLOW_LKH_SOURCE = r"""
import sys
import time
from pathlib import Path

parameters = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    key, _, value = line.partition("=")
    parameters[key.strip()] = value.strip()
with (Path(__file__).parent / "calls.log").open("a") as handle:
    handle.write(Path(parameters["PROBLEM_FILE"]).name + "\n")
print("* 1: Cost = 0_50, Time = 0.01 sec.", flush=True)
time.sleep(1.5)
"""

_SILENT_LKH_SOURCE = "print('Preprocessing time = 0.00 sec.')\n"


def _write_script(path: Path, source: str) -> Path:
    """Write an executable Python script with the current interpreter."""
    path.write_text(f"#!{sys.executable}\n{source}")
    path.chmod(0o755)
    return path


def _write_square_tsp(path: Path) -> Path:
    """Write a unit-square TSP whose identity tour has length 4."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "NAME : square\nTYPE : TSP\nDIMENSION : 4\nEDGE_WEIGHT_TYPE : EUC_2D\n"
        "NODE_COORD_SECTION\n1 0 0\n2 1 0\n3 1 1\n4 0 1\nEOF\n"
    )
    return path


def _write_suite(tmp_path: Path, entries: list[dict[str, object]]) -> Path:
    """Write a pilot suite file with two seeds."""
    path = tmp_path / "suite.json"
    path.write_text(json.dumps({"name": "t", "seeds": [1, 2], "instances": entries}))
    return path


def _record(
    instance: str, arm: str, seed: int, cost: int, **overrides: object
) -> dict[str, object]:
    """Build a feasible pilot record for report tests."""
    record: dict[str, object] = {
        "instance": instance,
        "kind": "SOP",
        "arm": arm,
        "seed": seed,
        "status": "ok",
        "penalty": 0,
        "cost": cost,
        "gap": cost / 100 - 1,
        "reached_bks": False,
        "validated_cost": None,
        "validation_error": None,
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------
# Reported-result runner
# ---------------------------------------------------------------------------


def test_read_parameter_file_keeps_bare_keywords(tmp_path: Path) -> None:
    path = tmp_path / "a.par"
    path.write_text("# note\nPROBLEM_FILE = x.sop\nSPECIAL\nruns : 10\nEOF\nSEED = 9\n")
    assert read_parameter_file(path) == {
        "PROBLEM_FILE": "x.sop",
        "SPECIAL": "",
        "RUNS": "10",
    }


def test_reported_run_merges_parameters_and_parses_penalty(tmp_path: Path) -> None:
    problem = _write_square_tsp(tmp_path / "square.tsp")
    script = _write_script(tmp_path / "fake_lkh", _FAKE_LKH_SOURCE)
    report = run_lkh_reported(
        problem,
        time_limit_seconds=2,
        base_parameters={
            "SPECIAL": "",
            "RUNS": "10",
            "INITIAL_TOUR_FILE": "start.tour",
            "MOVE_TYPE": "5 SPECIAL",
            "OPTIMUM": "90",
        },
        overrides={"MOVE_TYPE": "3 SPECIAL", "KICK_TYPE": 4},
        seed=7,
        executable=script,
    )
    assert (report.penalty, report.cost) == (0, 100)
    assert [(point.penalty, point.cost) for point in report.trace] == [
        (7, 200),
        (0, 100),
    ]
    assert report.trials == 5
    assert not report.killed
    assert not report.reached_optimum
    lines = (tmp_path / "lkh.parameters.saved").read_text().splitlines()
    assert "SPECIAL" in lines
    assert "RUNS = 1" in lines and "RUNS = 10" not in lines
    assert "SEED = 7" in lines
    assert not any(line.startswith("INITIAL_TOUR_FILE") for line in lines)
    assert [line for line in lines if line.startswith("MOVE_TYPE")] == [
        "MOVE_TYPE = 3 SPECIAL"
    ]
    assert lines.index("SPECIAL") < lines.index("MOVE_TYPE = 3 SPECIAL")


def test_reported_run_parses_plain_cost_and_validates_tour(tmp_path: Path) -> None:
    problem = _write_square_tsp(tmp_path / "square.tsp")
    script = _write_script(tmp_path / "fake_lkh", _FAKE_LKH_SOURCE)
    report = run_lkh_reported(
        problem,
        time_limit_seconds=2,
        overrides={"FAKE_COST": 4, "FAKE_PLAIN": 1, "OPTIMUM": 4},
        validate=True,
        executable=script,
    )
    assert (report.penalty, report.cost) == (0, 4)
    assert report.reached_optimum
    assert report.validated_cost == pytest.approx(4.0)
    assert report.validation_error is None


def test_reported_run_killed_at_deadline_keeps_best(tmp_path: Path) -> None:
    problem = _write_square_tsp(tmp_path / "square.tsp")
    script = _write_script(tmp_path / "stalling_lkh", _STALLING_LKH_SOURCE)
    report = run_lkh_reported(
        problem,
        time_limit_seconds=1,
        deadline_seconds=1.5,
        validate=True,
        executable=script,
    )
    assert report.killed
    assert (report.penalty, report.cost) == (3, 500)
    assert report.trials is None
    assert report.validated_cost is None
    assert report.validation_error is not None
    assert report.runtime_seconds < 10


def test_reported_run_without_solution_is_an_error(tmp_path: Path) -> None:
    problem = _write_square_tsp(tmp_path / "square.tsp")
    script = _write_script(tmp_path / "silent_lkh", _SILENT_LKH_SOURCE)
    with pytest.raises(SolverExecutionError, match="printed no solution"):
        run_lkh_reported(problem, time_limit_seconds=1, executable=script)


# ---------------------------------------------------------------------------
# Suite, arms and time budget
# ---------------------------------------------------------------------------


def test_arm_overrides_keep_special_moves() -> None:
    arm = ARMS[2]
    assert arm.overrides({"SPECIAL": ""})["MOVE_TYPE"] == f"{arm.move_type} SPECIAL"
    assert "SPECIAL" in str(arm.overrides({"MOVE_TYPE": "5 special"})["MOVE_TYPE"])
    assert arm.overrides({})["MOVE_TYPE"] == str(arm.move_type)


def test_load_suite_resolves_parameters_and_bks(tmp_path: Path) -> None:
    _write_square_tsp(tmp_path / "data" / "S" / "a.tsp")
    _write_square_tsp(tmp_path / "data" / "S" / "b.tsp")
    (tmp_path / "data" / "S" / "a.par").write_text(
        "PROBLEM_FILE = a.tsp\nSPECIAL\nOPTIMUM = 4\n"
    )
    suite_path = _write_suite(
        tmp_path,
        [
            {
                "kind": "sop",
                "tier": "mid",
                "problem": "S/a.tsp",
                "parameters": "S/a.par",
            },
            {"kind": "TSP", "tier": "large", "problem": "S/b.tsp", "bks": 2.5},
        ],
    )
    suite = load_suite(suite_path, data_root=tmp_path / "data")
    first, second = suite.instances
    assert (first.kind, first.bks, first.base_parameters["OPTIMUM"]) == ("SOP", 4, "4")
    assert "SPECIAL" in first.base_parameters
    assert second.bks == 2.5
    assert "OPTIMUM" not in second.base_parameters


def test_load_suite_reports_missing_files(tmp_path: Path) -> None:
    suite_path = _write_suite(
        tmp_path, [{"kind": "SOP", "tier": "mid", "problem": "S/missing.sop"}]
    )
    with pytest.raises(FileNotFoundError, match="missing files"):
        load_suite(suite_path, data_root=tmp_path / "data")


def test_blocks_and_worst_case_time(tmp_path: Path) -> None:
    _write_square_tsp(tmp_path / "data" / "a.tsp")
    _write_square_tsp(tmp_path / "data" / "b.tsp")
    suite = load_suite(
        _write_suite(
            tmp_path,
            [
                {"kind": "TSP", "tier": "mid", "problem": "a.tsp"},
                {"kind": "TSP", "tier": "large", "problem": "b.tsp"},
            ],
        ),
        data_root=tmp_path / "data",
    )
    blocks = build_blocks(suite)
    assert len(blocks) == 4
    assert all(len(block) == len(ARMS) for block in blocks)
    assert [block[0].seed for block in blocks] == [1, 1, 2, 2]
    per_seed = TIERS["mid"].deadline_seconds + TIERS["large"].deadline_seconds
    assert worst_case_seconds(blocks, jobs=4) == 2 * per_seed
    assert worst_case_seconds(blocks, jobs=2) == 4 * per_seed


def test_cli_refuses_a_suite_over_the_time_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_square_tsp(tmp_path / "a.tsp")
    suite_path = _write_suite(
        tmp_path, [{"kind": "TSP", "tier": "mid", "problem": str(tmp_path / "a.tsp")}]
    )
    arguments = ["lkh-pilot", "--suite", str(suite_path), "--dry-run"]
    assert main([*arguments, "--max-minutes", "0.5"]) == 1
    assert "exceeds --max-minutes" in capsys.readouterr().err
    assert main([*arguments, "--max-minutes", "2"]) == 0


# ---------------------------------------------------------------------------
# Runner and report
# ---------------------------------------------------------------------------


def test_run_pilot_writes_validated_records(tmp_path: Path) -> None:
    _write_square_tsp(tmp_path / "data" / "square.tsp")
    suite = load_suite(
        _write_suite(
            tmp_path,
            [
                {
                    "kind": "TSP",
                    "tier": "mid",
                    "problem": "square.tsp",
                    "bks": 4,
                    "base": {"FAKE_COST": 4},
                }
            ],
        ),
        data_root=tmp_path / "data",
    )
    script = _write_script(tmp_path / "fake_lkh", _FAKE_LKH_SOURCE)
    run_dir = tmp_path / "run"
    blocks = build_blocks(PilotSuite(suite.name, (1,), suite.instances))
    progress = run_pilot(blocks, run_dir, jobs=4, executable=script)
    records = progress.records

    assert progress.finished and not progress.stopped
    assert [record.arm for record in records] == [arm.name for arm in ARMS]
    for record in records:
        assert record.status == "ok"
        assert (record.penalty, record.cost) == (0, 4)
        assert record.reached_bks is True
        assert record.gap == pytest.approx(0.0)
        assert record.validated_cost == pytest.approx(4.0)
        assert record.trace_file is not None
        assert (run_dir / record.trace_file).is_file()
        assert record.log_file is not None
        assert "KICK_TYPE" in (run_dir / record.log_file).read_text()
    assert len(load_records(run_dir)) == len(ARMS)
    assert (run_dir / "config.json").is_file()
    assert instance_outcomes(load_records(run_dir))[0].state == "saturated"
    with pytest.raises(FileExistsError):
        run_pilot(blocks, run_dir, jobs=4, executable=script)


def test_verdict_go_when_clear_winner_changes() -> None:
    records = [
        _record("a", "m5", 1, 100),
        _record("a", "m5", 2, 101),
        _record("a", "m3", 1, 110),
        _record("a", "m3", 2, 112),
        _record("b", "m5", 1, 130),
        _record("b", "m5", 2, 131),
        _record("b", "m3", 1, 120),
        _record("b", "m3", 2, 121),
    ]
    outcomes = instance_outcomes(records)
    assert [(o.winner, o.clear, o.state) for o in outcomes] == [
        ("m5", True, "decided"),
        ("m3", True, "decided"),
    ]
    assert verdict(outcomes).startswith("GO")
    assert "GO:" in summarize_pilot(records)


def test_verdict_no_go_and_noise() -> None:
    dominated = [
        _record(name, arm, seed, cost + seed)
        for name in ("a", "b")
        for arm, cost in (("m5", 100), ("m3", 120))
        for seed in (1, 2)
    ]
    assert verdict(instance_outcomes(dominated)).startswith("NO-GO")
    overlapping = [
        _record("a", "m5", 1, 100),
        _record("a", "m5", 2, 120),
        _record("a", "m3", 1, 110),
        _record("a", "m3", 2, 115),
    ]
    assert verdict(instance_outcomes(overlapping)).startswith("INCONCLUSIVE")


def test_tied_instances_have_no_winner() -> None:
    records = [_record("a", arm, seed, 100) for arm in ("m5", "m3") for seed in (1, 2)]
    outcome = instance_outcomes(records)[0]
    assert (outcome.winner, outcome.state) == (None, "tied")
    assert verdict([outcome]).startswith("NO VERDICT")


def test_verdict_does_not_pool_types_that_share_arms() -> None:
    records = [
        _record(name, arm, seed, cost + seed, kind=kind)
        for kind, names, costs in (
            ("SOP", ("s1", "s2"), {"m5_kick6": 100, "m5_kick4": 120}),
            ("CVRP", ("c1", "c2"), {"m5_kick6": 120, "m5_kick4": 100}),
        )
        for name in names
        for arm, cost in costs.items()
        for seed in (1, 2)
    ]
    lines = verdict(instance_outcomes(records)).splitlines()
    assert lines[0] == "CVRP: NO-GO: m5_kick4 wins on all 2 decided instances."
    assert lines[1] == "SOP: NO-GO: m5_kick6 wins on all 2 decided instances."
    assert lines[2].startswith("Between types the most frequent winner differs")
    assert not any(line.startswith("GO") or ": GO" in line for line in lines)


def test_verdict_judges_each_arm_set_apart() -> None:
    tsp = [
        _record("t", arm, seed, cost + seed, kind="TSP")
        for arm, cost in (("m5_walk", 100), ("m5_kick4", 120))
        for seed in (1, 2)
    ]
    sop = [
        _record("s", arm, seed, cost + seed)
        for arm, cost in (("m5_kick6", 100), ("m5_kick4", 120))
        for seed in (1, 2)
    ]
    lines = verdict(instance_outcomes([*tsp, *sop])).splitlines()
    assert lines[:2] == [
        "SOP: NO-GO: m5_kick6 wins on all 1 decided instances.",
        "TSP: NO-GO: m5_walk wins on all 1 decided instances.",
    ]


def test_single_seed_wins_are_never_clear() -> None:
    records = [_record("a", "m5", 1, 100), _record("a", "m3", 1, 150)]
    outcome = instance_outcomes(records)[0]
    assert (outcome.winner, outcome.clear) == ("m5", False)
    assert verdict([outcome]).startswith("INCONCLUSIVE")


def test_infeasible_invalid_and_incomplete_instances_do_not_count() -> None:
    infeasible = [
        _record("a", "m5", 1, 100),
        _record("a", "m3", 1, 90, penalty=4, gap=None),
    ]
    invalid = [
        _record("b", "m5", 1, 100, validation_error="route 1 carries 9"),
        _record("b", "m3", 1, 110, validated_cost=110.0),
    ]
    incomplete = [
        _record("c", "m5", 1, 100),
        _record("c", "m3", 1, 0, status="error", penalty=None, gap=None),
    ]
    outcomes = instance_outcomes([*infeasible, *invalid, *incomplete])
    assert [outcome.state for outcome in outcomes] == [
        "infeasible",
        "invalid",
        "incomplete",
    ]
    assert verdict(outcomes).startswith("NO VERDICT")


# ---------------------------------------------------------------------------
# Stopping and resuming
# ---------------------------------------------------------------------------


def _two_instance_blocks(tmp_path: Path) -> tuple:
    """Return the blocks of a two-instance, one-seed TSP suite."""
    _write_square_tsp(tmp_path / "data" / "a.tsp")
    _write_square_tsp(tmp_path / "data" / "b.tsp")
    suite = load_suite(
        _write_suite(
            tmp_path,
            [
                {"kind": "TSP", "tier": "mid", "problem": "a.tsp"},
                {"kind": "TSP", "tier": "mid", "problem": "b.tsp"},
            ],
        ),
        data_root=tmp_path / "data",
    )
    return build_blocks(PilotSuite(suite.name, (1,), suite.instances))


def _send_interrupt_once_running(run_dir: Path) -> threading.Thread:
    """Send SIGINT to this process once the pilot has created its files."""

    def send() -> None:
        deadline = time.monotonic() + 20
        while not (run_dir / "runs.jsonl").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.3)
        os.kill(os.getpid(), signal.SIGINT)

    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    return thread


def test_stop_after_block_then_resume_runs_only_the_rest(tmp_path: Path) -> None:
    blocks = _two_instance_blocks(tmp_path)
    script = _write_script(tmp_path / "slow_lkh", _SLOW_LKH_SOURCE)
    run_dir = tmp_path / "run"
    sender = _send_interrupt_once_running(run_dir)
    first = run_pilot(
        blocks, run_dir, executable=script, on_interrupt=lambda: STOP_AFTER_BLOCK
    )
    sender.join()
    assert first.stopped and not first.finished
    assert (first.completed_blocks, first.total_blocks) == (1, 2)
    assert pilot_progress(run_dir) == (1, 2)
    assert {record.instance for record in first.records} == {"a"}

    calls = tmp_path / "calls.log"
    calls.unlink()
    second = run_pilot(blocks, run_dir, executable=script, resume=True)
    assert second.finished and not second.stopped
    assert len(second.records) == 2 * len(ARMS)
    assert len(load_records(run_dir)) == 2 * len(ARMS)
    assert set(calls.read_text().split()) == {"b.tsp"}


def test_stop_now_discards_the_block_in_progress(tmp_path: Path) -> None:
    blocks = _two_instance_blocks(tmp_path)
    script = _write_script(tmp_path / "stalling_lkh", _STALLING_LKH_SOURCE)
    run_dir = tmp_path / "run"
    sender = _send_interrupt_once_running(run_dir)
    started = time.monotonic()
    progress = run_pilot(
        blocks, run_dir, executable=script, on_interrupt=lambda: STOP_NOW
    )
    sender.join()
    assert time.monotonic() - started < 15
    assert progress.stopped
    assert progress.completed_blocks == 0
    assert load_records(run_dir) == []


def test_waves_fill_the_jobs_and_keep_tiers_apart(tmp_path: Path) -> None:
    for name in "abcd":
        _write_square_tsp(tmp_path / "data" / f"{name}.tsp")
    suite = load_suite(
        _write_suite(
            tmp_path,
            [
                {"kind": "TSP", "tier": tier, "problem": f"{name}.tsp"}
                for name, tier in zip(
                    "abcd", ("mid", "mid", "mid", "large"), strict=True
                )
            ],
        ),
        data_root=tmp_path / "data",
    )
    blocks = build_blocks(PilotSuite(suite.name, (1,), suite.instances))
    waves = build_waves(blocks, jobs=8)
    assert [[block[0].instance.name for block in wave] for wave in waves] == [
        ["a", "b"],
        ["c"],
        ["d"],
    ]
    mid, large = TIERS["mid"].deadline_seconds, TIERS["large"].deadline_seconds
    assert worst_case_seconds(blocks, jobs=8) == 2 * mid + large
    assert worst_case_seconds(blocks, jobs=12) == mid + large
    with pytest.raises(ValueError, match="distinct"):
        build_blocks(PilotSuite(suite.name, (1, 1), suite.instances))

    script = _write_script(tmp_path / "fake_lkh", _FAKE_LKH_SOURCE)
    progress = run_pilot(blocks, tmp_path / "run", jobs=8, executable=script)
    assert progress.finished
    assert len(progress.records) == 4 * len(ARMS)


def test_resume_survives_a_cut_off_record_but_not_a_new_binary(tmp_path: Path) -> None:
    blocks = _two_instance_blocks(tmp_path)
    script = _write_script(tmp_path / "fake_lkh", _FAKE_LKH_SOURCE)
    run_dir = tmp_path / "run"
    run_pilot(blocks[:1] + blocks[1:], run_dir, executable=script)
    with (run_dir / "runs.jsonl").open("a") as handle:
        handle.write('{"instance": "b", "se')
    assert pilot_progress(run_dir) == (2, 2)
    assert run_pilot(blocks, run_dir, executable=script, resume=True).finished

    other = _write_script(tmp_path / "other_lkh", _FAKE_LKH_SOURCE + "\n# rebuilt\n")
    with pytest.raises(ValueError, match="different LKH binary"):
        run_pilot(blocks, run_dir, executable=other, resume=True)


def test_resume_refuses_a_different_plan_and_restart_archives(tmp_path: Path) -> None:
    blocks = _two_instance_blocks(tmp_path)
    script = _write_script(tmp_path / "fake_lkh", _FAKE_LKH_SOURCE)
    run_dir = tmp_path / "run"
    run_pilot(blocks, run_dir, executable=script)
    with pytest.raises(ValueError, match="different plan"):
        run_pilot(blocks[:1], run_dir, executable=script, resume=True)

    archive = archive_run(run_dir)
    assert (archive / "runs.jsonl").is_file()
    assert (archive / "traces").is_dir()
    assert pilot_progress(run_dir) is None
    assert run_pilot(blocks, run_dir, executable=script).finished


def test_cli_needs_a_decision_for_an_existing_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_square_tsp(tmp_path / "a.tsp")
    suite_path = _write_suite(
        tmp_path, [{"kind": "TSP", "tier": "mid", "problem": str(tmp_path / "a.tsp")}]
    )
    script = _write_script(tmp_path / "fake_lkh", _FAKE_LKH_SOURCE)
    monkeypatch.setenv("LKH_BIN", str(script))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    run_dir = tmp_path / "run"
    arguments = ["lkh-pilot", "--suite", str(suite_path), "--out", str(run_dir)]
    assert main(arguments) == 0
    assert main(arguments) == 1
    assert "--resume or --restart" in capsys.readouterr().err
    assert main([*arguments, "--resume"]) == 0
    assert main([*arguments, "--restart"]) == 0
    assert len(list(run_dir.glob("superseded-*"))) == 1


def test_constrained_types_compare_kick_sizes(tmp_path: Path) -> None:
    _write_square_tsp(tmp_path / "data" / "a.sop")
    suite = load_suite(
        _write_suite(tmp_path, [{"kind": "SOP", "tier": "mid", "problem": "a.sop"}]),
        data_root=tmp_path / "data",
    )
    arms = [run.arm for run in build_blocks(suite)[0]]
    assert tuple(arms) == CONSTRAINED_ARMS
    assert all(arm.kick_type != 0 for arm in arms)


# ---------------------------------------------------------------------------
# Benchmark archives
# ---------------------------------------------------------------------------


def test_fetch_lkh_benchmarks_unpacks_and_records(tmp_path: Path) -> None:
    calls: list[str] = []

    def downloader(url: str, destination: Path) -> None:
        calls.append(url)
        payload = b"NAME : tiny\n"
        with tarfile.open(destination, "w:gz") as archive:
            member = tarfile.TarInfo("SOP/INSTANCES/tiny.sop")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

    manifest_path = tmp_path / "manifest.json"
    arguments = {
        "data_root": tmp_path / "data",
        "base_url": "http://example.test/BENCHMARKS/",
        "manifest_path": manifest_path,
        "downloader": downloader,
    }
    entries = fetch_lkh_benchmarks(["SOP"], **arguments)
    unpacked = tmp_path / "data" / "LKH3" / "SOP" / "SOP" / "INSTANCES" / "tiny.sop"
    assert unpacked.read_text() == "NAME : tiny\n"
    assert entries["SOP"]["source"] == "http://example.test/BENCHMARKS/SOP.tgz"
    assert json.loads(manifest_path.read_text())["entries"]["SOP"]["sha256"]

    fetch_lkh_benchmarks(["SOP"], **arguments)
    assert calls == ["http://example.test/BENCHMARKS/SOP.tgz"]
