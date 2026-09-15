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
    """Map retained inference rows back to their requested symbol identities.

    Example:
        provider = ModelSignalProvider(model=inference_model)
        scores = provider.scores(
            ts_us=1,
            data_view=data_view,
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
        data_view: MarketDataView,
        symbols: Sequence[str],
    ) -> dict[str, float]:
        """Return scores only for rows retained by fitted preprocessing.

        Example:
            scores = provider.scores(
                ts_us=1,
                data_view=data_view,
                symbols=("600000", "000001"),
            )
        """
        features = data_view.get_feature_matrix(symbols)

        if self.diagnostics:
            self.diagnostics.on_before_predict(
                ts_us=ts_us,
                features=features,
                model=self.model,
            )

        keep_rows, predictions = self.model.predict(features)
        retained_symbols = (
            str(symbol)
            for symbol, keep in zip(symbols, keep_rows, strict=True)
            if bool(keep)
        )

        scores = {
            symbol: float(score)
            for symbol, score in zip(retained_symbols, predictions, strict=True)
        }

        if self.diagnostics:
            self.diagnostics.on_after_predict(
                ts_us=ts_us,
                scores=scores,
                requested_count=len(symbols),
                skipped_count=len(symbols) - len(scores),
            )

        return scores
