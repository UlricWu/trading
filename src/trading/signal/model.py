# filepath: src/trading/signal/model.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.trading.market.data_view import MarketDataView
from src.trading.signal.base import SignalProvider
from src.trading.signal.diagnostics import SignalDiagnostics
from src.training.inference_model import InferenceModel


@dataclass(frozen=True, slots=True)
class ModelSignalProvider(SignalProvider):
    model: InferenceModel
    diagnostics: SignalDiagnostics | None = None

    def name(self) -> str:
        return "model"

    def scores(
            self,
            *,
            ts_us: int,
            data_view: MarketDataView,
            symbols: Sequence[str],
    ) -> dict[str, float]:
        features = data_view.get_feature_matrix(symbols)

        if self.diagnostics:
            self.diagnostics.on_before_predict(
                ts_us=ts_us,
                features=features,
                model=self.model,
            )

        predictions = self.model.predict(features)
        if len(predictions) != len(symbols):
            raise RuntimeError(
                "prediction count must equal requested symbol count: "
                f"predictions={len(predictions)} symbols={len(symbols)}"
            )

        scores = {
            str(symbol): float(score)
            for symbol, score in zip(symbols, predictions, strict=True)
        }

        if self.diagnostics:
            self.diagnostics.on_after_predict(
                ts_us=ts_us,
                scores=scores,
            )

        return scores
