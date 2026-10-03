"""Anytime CVRP benchmark runner for the bundled solvers."""

from .metrics import gap_at, primal_integral, time_to_target
from .runner import RunRecord, RunSpec, run_specs
from .suites import BksProvenance, bks_provenance, load_bks, suite_instances

__all__ = [
    "BksProvenance",
    "RunRecord",
    "RunSpec",
    "bks_provenance",
    "gap_at",
    "load_bks",
    "primal_integral",
    "run_specs",
    "suite_instances",
    "time_to_target",
]
