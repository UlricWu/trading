# filepath: src/trading/signal/diagnostics.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from src import logs
from src.utils.datetime_utils import DateTimeUtils


class FeatureNamedModel(Protocol):
    feature_names: Sequence[str]


class SignalDiagnostics(Protocol):
    def on_before_predict(
        self,
        *,
        ts_us: int,
        features: np.ndarray,
        model: FeatureNamedModel,
    ) -> None: ...

    def on_after_predict(
        self,
        *,
        ts_us: int,
        scores: Mapping[str, float],
    ) -> None: ...


@dataclass(slots=True)
class BasicSignalDiagnostics:
    """
    Pure diagnostic observer (matrix version)

    - no dependency on InferenceModel internals
    - no mutation
    - no trading semantics
    """

    log_daily_only: bool = True
    score_thresholds: tuple[float, ...] = ()
    _last_feature_logged_date: str | None = None
    _last_score_logged_date: str | None = None

    # ==========================================================
    # Before predict
    # ==========================================================

    def on_before_predict(
        self,
        *,
        ts_us: int,
        features: np.ndarray,
        model: FeatureNamedModel,
    ) -> None:

        if not self._should_log(channel="feature", ts_us=ts_us):
            return

        if features is None or features.size == 0:
            return

        feature_names = model.feature_names
        if not feature_names:
            return

        X = features
        n_syms, n_feats = X.shape

        mask = ~np.isfinite(X)
        total_missing = int(mask.sum())
        total = n_syms * n_feats
        missing_ratio = total_missing / float(total) if total else 0.0

        miss_by_feat = mask.sum(axis=0)
        miss_by_sym = mask.sum(axis=1)

        top_idx = np.argsort(-miss_by_feat)[:10]
        top = [
            f"{feature_names[i]}:{int(miss_by_feat[i])}"
            for i in top_idx
            if miss_by_feat[i] > 0
        ]
        top_str = ", ".join(top) or "none"

        logs.info(
            "[FeatureMissing] "
            f"ts_us={ts_us} "
            f"n_sym={n_syms} "
            f"n_feat={n_feats} "
            f"missing_ratio={missing_ratio:.4%} "
            f"sym_missing_min={int(miss_by_sym.min())} "
            f"p50={int(np.percentile(miss_by_sym, 50))} "
            f"p95={int(np.percentile(miss_by_sym, 95))} "
            f"max={int(miss_by_sym.max())} "
            f"top_missing_feats={top_str}"
        )

    # ==========================================================
    # After predict
    # ==========================================================

    def on_after_predict(
        self,
        *,
        ts_us: int,
        scores: Mapping[str, float],
    ) -> None:

        if not self._should_log(channel="score", ts_us=ts_us):
            return

        if not scores:
            return

        arr = np.asarray(list(scores.values()), dtype=float)
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            return

        threshold_fields = _format_threshold_fields(
            scores=finite,
            thresholds=self.score_thresholds,
        )
        logs.info(
            "[ScoreDist] "
            f"ts_us={ts_us} "
            f"n={arr.size} "
            f"finite_n={finite.size} "
            f"min={finite.min():.6f} "
            f"p5={np.percentile(finite, 5):.6f} "
            f"p50={np.percentile(finite, 50):.6f} "
            f"mean={finite.mean():.6f} "
            f"p95={np.percentile(finite, 95):.6f} "
            f"max={finite.max():.6f}"
            f"{threshold_fields}"
        )

    # ==========================================================
    # Internal
    # ==========================================================

    def _should_log(self, *, channel: str, ts_us: int) -> bool:
        if not self.log_daily_only:
            return True

        today = DateTimeUtils.from_utc_epoch_us(int(ts_us)).date().isoformat()

        attr = f"_last_{channel}_logged_date"
        if today != getattr(self, attr):
            setattr(self, attr, today)
            return True

        return False


def _format_threshold_fields(
    *,
    scores: np.ndarray,
    thresholds: tuple[float, ...],
) -> str:
    """Format configured score-threshold hit counts for operational logs."""
    fields: list[str] = []
    for index, threshold in enumerate(thresholds):
        value = float(threshold)
        if not np.isfinite(value):
            continue
        hits = int((scores >= value).sum())
        ratio = hits / float(scores.size) if scores.size else 0.0
        fields.append(
            f" threshold_{index}={value:.6f} "
            f"threshold_{index}_hits={hits} "
            f"threshold_{index}_hit_ratio={ratio:.2%}"
        )
    return "".join(fields)
