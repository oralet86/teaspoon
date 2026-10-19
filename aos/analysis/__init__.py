"""Turn raw run directories into journal data (CSV) and figures (PNG)."""

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
JOURNAL = REPOSITORY_ROOT / "journal"


def journal_paths(claim: int = 1) -> tuple[Path, Path]:
    """Return (and create) the data and figure directories of a claim."""
    data = JOURNAL / "data" / f"claim-{claim}"
    figures = JOURNAL / "figures" / f"claim-{claim}"
    data.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    return data, figures


def source_note(data_files: list[str], script: str, seeds: str) -> str:
    """Return the caption line every figure carries (working rule)."""
    return f"Data: {', '.join(data_files)} | Script: {script} | {seeds}"
