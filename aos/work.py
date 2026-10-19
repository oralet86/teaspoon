"""Counted work, logged beside the timer as a cross-check."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Work:
    """Operation counts accumulated by operators.

    Attributes:
        lookups: Distance-matrix entries read.
        moved: Tour positions rewritten (list elements moved or swapped).
    """

    lookups: int = 0
    moved: int = 0
