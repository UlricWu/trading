# filepath: src/trading/signal/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

import numpy as np


class SignalProvider(ABC):
    """
    Signal provider contract.

    Contract:
    - produces scores only (symbol -> float)
    - must NOT output targets
    - must NOT be aware of execution constraints (T+1/limits/cash)

    Example:
        provider: SignalProvider = ModelSignalProvider(model=inference_model)
        scores = provider.scores(ts_us=1, features=features, symbols=("600000",))
    """

    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def scores(
        self,
        *,
        ts_us: int,
        features: np.ndarray,
        symbols: Sequence[str],
    ) -> dict[str, float]:
        """Score prepared feature rows in the supplied symbol order.

        Example:
            scores = provider.scores(ts_us=1, features=features, symbols=("600000",))
        """
        ...
