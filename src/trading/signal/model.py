# filepath: src/trading/signal/model.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from src.trading.signal.base import SignalProvider
from src.trading.signal.diagnostics import SignalDiagnostics
from src.training.inference_model import InferenceModel


@dataclass(frozen=True, slots=True)
class ModelSignalProvider(SignalProvider):
    """Map aligned predictions to symbols, skipping ordinary missing predictions.

    Example:
        provider = ModelSignalProvider(model=inference_model)
        scores = provider.scores(
            ts_us=1,
            features=features,
            symbols=("600000",),
        )
    """

    model: InferenceModel
    diagnostics: SignalDiagnostics | None = None

    def name(self) -> str:
        """Return the public signal-provider name.

        Example:
            provider_name = provider.name()
        """
        return "model"

    def scores(
        self,
        *,
        ts_us: int,
        features: np.ndarray,
        symbols: Sequence[str],
    ) -> dict[str, float]:
        """Return scores only for rows retained by fitted preprocessing.

        Example:
            scores = provider.scores(
                ts_us=1,
                features=features,
                symbols=("600000", "000001"),
            )
        """
        if self.diagnostics:
            self.diagnostics.on_before_predict(
                ts_us=ts_us,
                features=features,
                model=self.model,
            )

        predictions = self.model.predict(features)
        scores = {
            str(symbol): float(score)
            for symbol, score in zip(symbols, predictions, strict=True)
            if not np.isnan(score)
        }

        if self.diagnostics:
            self.diagnostics.on_after_predict(
                ts_us=ts_us,
                scores=scores,
                requested_count=len(symbols),
                skipped_count=len(symbols) - len(scores),
            )

        return scores
