# filepath: tests/trading/signal/test_model.py
"""Symbol-identity tests for fitted model signals."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from src.trading.signal.model import ModelSignalProvider
from src.training.engines.preprocessing import FittedPreprocessor
from src.training.inference_model import InferenceModel


class _FirstColumnModel:
    def predict(self, values: np.ndarray) -> np.ndarray:
        return values[:, 0]


class _DataView:
    def get_feature_matrix(self, symbols: object) -> np.ndarray:
        return np.array([[1.0], [np.nan], [3.0]])


class _Diagnostics:
    def __init__(self) -> None:
        self.after: tuple[Mapping[str, float], int, int] | None = None

    def on_before_predict(self, **values: object) -> None:
        return None

    def on_after_predict(
        self,
        *,
        ts_us: int,
        scores: Mapping[str, float],
        requested_count: int,
        skipped_count: int,
    ) -> None:
        self.after = (dict(scores), requested_count, skipped_count)


def test_scores_map_only_retained_rows_to_their_original_symbols() -> None:
    diagnostics = _Diagnostics()
    provider = ModelSignalProvider(
        model=InferenceModel(
            model=_FirstColumnModel(),
            preprocess=FittedPreprocessor(
                feature_names=("factor",),
                missing_method="drop",
            ),
            feature_set="daily",
            feature_version="v1",
        ),
        diagnostics=diagnostics,
    )

    scores = provider.scores(
        ts_us=1,
        data_view=_DataView(),  # type: ignore[arg-type]
        symbols=("600000", "000001", "600001"),
    )

    assert scores == {"600000": 1.0, "600001": 3.0}
    assert diagnostics.after == (scores, 3, 1)
