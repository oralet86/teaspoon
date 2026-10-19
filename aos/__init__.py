# ruff: noqa: E402
"""Scalar operator-selection study for the symmetric TSP (Claim 1).

Everything on the hot path is plain Python (lists, the ``random`` module) so
that measured time is proportional to the work an operator does.

The E1 pipeline: :mod:`aos.e1_states` builds the measured tours,
:mod:`aos.e1_marginal` measures every operator's marginal rate on them
(:mod:`aos.marginal`), and :mod:`aos.analysis.e1_marginal` writes the journal
data and figures.  :mod:`aos.e0` and :mod:`aos.e0_contention` check the clock.

:mod:`aos.population` and :mod:`aos.slices` hold the steady-state population
search with time slices, kept for the full runs of E2 to E5.  Time-bounded
runs are not bit-reproducible; every slice logs its application count, so a
run can be replayed exactly in count mode.
"""

import os

# Timed runs must stay on one core; numerical libraries read these when first
# imported, so they are set before anything below imports NumPy.  Existing
# values are kept.
for _variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_variable, "1")

from .operators import OPERATORS, Application, Operator, descend
from .population import (
    AdaptivePursuit,
    FixedArm,
    PopulationConfig,
    PopulationResult,
    ProbabilityMatching,
    ReplayArm,
    UniformArm,
    gain_rate,
    run_population,
)
from .problem import Problem
from .slices import SliceRecord, run_slice
from .tour import Tour
from .work import Work

__all__ = [
    "OPERATORS",
    "AdaptivePursuit",
    "Application",
    "FixedArm",
    "Operator",
    "PopulationConfig",
    "PopulationResult",
    "ProbabilityMatching",
    "Problem",
    "ReplayArm",
    "SliceRecord",
    "Tour",
    "UniformArm",
    "Work",
    "descend",
    "gain_rate",
    "run_population",
    "run_slice",
]
