"""Offscreen smoke tests for the Qt viewer."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from instances import build_catalog
from viewer import MainWindow

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def application() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


def test_window_loads_instance_with_tour(application: QApplication) -> None:
    entries = build_catalog(DATA_ROOT)
    entry = next(item for item in entries if item.label == "a280.tsp")
    window = MainWindow(DATA_ROOT)
    window.load_entry(entry)
    assert window.instance is not None
    assert window.instance.dimension == 280
    assert len(window.instance.sequences) == 1
    window.close()


def test_window_loads_matrix_only_instance(application: QApplication) -> None:
    entries = build_catalog(DATA_ROOT)
    entry = next(item for item in entries if item.label == "brazil58.tsp")
    window = MainWindow(DATA_ROOT)
    window.load_entry(entry)
    assert window.instance is not None
    assert window.instance.nodes is not None
    assert len(window.instance.matrices) == 1
    window.close()
