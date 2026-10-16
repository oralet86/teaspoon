"""Operator selection strategies.

A selector decides which mutation operator the next offspring gets and
learns from the reward that operator earned.  To add a strategy, implement
the two methods of :class:`OperatorSelector` and register a factory in
:data:`SELECTORS`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

import numpy as np


class OperatorSelector(Protocol):
    """Chooses an operator by index and is told what it earned."""

    def select(self, rng: np.random.Generator) -> int:
        """Return the index of the operator to apply next."""
        ...

    def update(self, operator: int, reward: float) -> None:
        """Record the reward the operator earned on its last use."""
        ...


class FixedSelector:
    """Always applies the same operator; the non-adaptive reference."""

    def __init__(self, operator: int) -> None:
        self._operator = operator

    def select(self, rng: np.random.Generator) -> int:
        """Return the fixed operator."""
        return self._operator

    def update(self, operator: int, reward: float) -> None:
        """Ignore the reward."""


class UniformSelector:
    """Picks every operator with equal probability.

    This is the baseline an adaptive strategy has to beat: it uses the same
    operators without learning anything.
    """

    def __init__(self, count: int) -> None:
        self._count = count

    def select(self, rng: np.random.Generator) -> int:
        """Return a uniformly random operator."""
        return int(rng.integers(self._count))

    def update(self, operator: int, reward: float) -> None:
        """Ignore the reward."""


class ProbabilityMatching:
    """Picks operators in proportion to their estimated quality.

    Each operator keeps a running quality estimate.  Selection probabilities
    follow the estimates, with a floor so that no operator is ever dropped.

    Args:
        count: Number of operators.
        minimum_probability: Floor on every operator's probability.
        learning_rate: Weight of the newest reward in the quality estimate.
    """

    def __init__(
        self, count: int, minimum_probability: float = 0.05, learning_rate: float = 0.1
    ) -> None:
        self._minimum = minimum_probability
        self._learning_rate = learning_rate
        self.quality = np.zeros(count)
        self.probabilities = np.full(count, 1.0 / count)

    def select(self, rng: np.random.Generator) -> int:
        """Sample an operator from the current probabilities."""
        return int(rng.choice(self.probabilities.size, p=self.probabilities))

    def update(self, operator: int, reward: float) -> None:
        """Move the operator's quality towards the reward and re-normalise."""
        self.quality[operator] += self._learning_rate * (
            reward - self.quality[operator]
        )
        total = self.quality.sum()
        count = self.quality.size
        if total <= 0:
            self.probabilities = np.full(count, 1.0 / count)
            return
        self.probabilities = (
            self._minimum + (1.0 - count * self._minimum) * self.quality / total
        )


class AdaptivePursuit:
    """Pushes probability towards the operator with the best quality.

    Where probability matching spreads probability by relative quality,
    pursuit moves it to the current leader, so it commits faster and reacts
    faster when the leader changes.

    Args:
        count: Number of operators.
        minimum_probability: Floor on every operator's probability.
        learning_rate: Weight of the newest reward in the quality estimate.
        pursuit_rate: How far probabilities move towards their target on
            each update.
    """

    def __init__(
        self,
        count: int,
        minimum_probability: float = 0.05,
        learning_rate: float = 0.1,
        pursuit_rate: float = 0.1,
    ) -> None:
        self._minimum = minimum_probability
        self._maximum = 1.0 - (count - 1) * minimum_probability
        self._learning_rate = learning_rate
        self._pursuit_rate = pursuit_rate
        self.quality = np.zeros(count)
        self.probabilities = np.full(count, 1.0 / count)

    def select(self, rng: np.random.Generator) -> int:
        """Sample an operator from the current probabilities."""
        return int(rng.choice(self.probabilities.size, p=self.probabilities))

    def update(self, operator: int, reward: float) -> None:
        """Update the quality estimate and pursue the best operator."""
        self.quality[operator] += self._learning_rate * (
            reward - self.quality[operator]
        )
        targets = np.full(self.quality.size, self._minimum)
        targets[int(self.quality.argmax())] = self._maximum
        self.probabilities += self._pursuit_rate * (targets - self.probabilities)


SELECTORS: Mapping[str, Callable[[int], OperatorSelector]] = {
    "uniform": UniformSelector,
    "probability-matching": ProbabilityMatching,
    "adaptive-pursuit": AdaptivePursuit,
}
"""Selector factories by name; each takes the number of operators."""


def make_selector(name: str, operator_names: tuple[str, ...]) -> OperatorSelector:
    """Build a selector from its name.

    Args:
        name: A key of :data:`SELECTORS`, or ``fixed:<operator>`` to always
            apply one operator.
        operator_names: The operators the selector chooses between.

    Raises:
        ValueError: If the selector or the fixed operator is unknown.
    """
    if name.startswith("fixed:"):
        operator = name.removeprefix("fixed:")
        if operator not in operator_names:
            raise ValueError(
                f"unknown operator {operator!r}; expected one of "
                f"{', '.join(operator_names)}"
            )
        return FixedSelector(operator_names.index(operator))
    if name not in SELECTORS:
        raise ValueError(
            f"unknown selector {name!r}; expected one of "
            f"{', '.join(SELECTORS)} or fixed:<operator>"
        )
    return SELECTORS[name](len(operator_names))
