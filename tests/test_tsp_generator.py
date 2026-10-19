"""Tests for the TSP generators (DIMACS and tspgen wrappers)."""

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from aos import Problem
from generators.dimacs import DEFAULT_DIMACS_DIRECTORY, DimacsGenerator
from generators.tsp import Kind, generate
from instances import load_distance_matrix

DIMACS_BUILT = all(
    os.access(DEFAULT_DIMACS_DIRECTORY / generator.value, os.X_OK)
    for generator in DimacsGenerator
)
RSCRIPT = shutil.which("Rscript")
TSPGEN_INSTALLED = (
    RSCRIPT is not None
    and subprocess.run(
        [RSCRIPT, "-e", "library(tspgen)"], capture_output=True
    ).returncode
    == 0
)

needs_dimacs = pytest.mark.skipif(
    not DIMACS_BUILT, reason="the DIMACS generators are not built"
)
needs_tspgen = pytest.mark.skipif(
    not TSPGEN_INSTALLED, reason="R or the tspgen and netgen packages are missing"
)

CASES = [
    pytest.param(Kind.UNIFORM, 60, marks=needs_dimacs),
    pytest.param(Kind.CLUSTERED, 60, marks=needs_tspgen),
    pytest.param(Kind.RANDOM_MATRIX, 60, marks=needs_dimacs),
    pytest.param(Kind.TSPGEN, 60, marks=needs_tspgen),
]


@pytest.mark.parametrize(("kind", "n"), CASES)
def test_generated_instance_is_valid_tsplib(kind: Kind, n: int, tmp_path: Path) -> None:
    path = generate(kind, n, seed=3, output_dir=tmp_path)
    matrix = load_distance_matrix(path)
    assert matrix.shape == (n, n)
    assert np.array_equal(matrix, matrix.T)
    assert np.all(np.diag(matrix) == 0)
    problem = Problem.from_tsplib(path)
    assert problem.n == n
    assert problem.optimum is None


@pytest.mark.parametrize(("kind", "n"), CASES)
def test_generation_is_reproducible(kind: Kind, n: int, tmp_path: Path) -> None:
    first = generate(kind, n, seed=1, output_dir=tmp_path / "a")
    second = generate(kind, n, seed=1, output_dir=tmp_path / "b")
    other = generate(kind, n, seed=2, output_dir=tmp_path / "c")
    assert first.read_text() == second.read_text()
    assert load_distance_matrix(first).tolist() != load_distance_matrix(other).tolist()


@needs_dimacs
def test_uniform_matches_the_challenge_generator(tmp_path: Path) -> None:
    path = generate(Kind.UNIFORM, 100, seed=1000, output_dir=tmp_path)
    reference = subprocess.run(
        [str(DEFAULT_DIMACS_DIRECTORY / "portgen"), "100", "1000"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert path.name == "portgen-100-1000.tsp"
    assert path.read_text() == reference


def test_too_small_instance_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        generate(Kind.UNIFORM, 5, seed=0, output_dir=tmp_path)
    with pytest.raises(ValueError):
        generate(Kind.TSPGEN, 5, seed=0, output_dir=tmp_path)
