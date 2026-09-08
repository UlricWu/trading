# filepath: src/trading/signal/diagnostics.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from src import logs
from src.training.inference_model import InferenceModel
from src.utils.datetime_utils import DateTimeUtils


class SignalDiagnostics(Protocol):
    """Observe raw feature and scored-row facts without changing them.

    Example:
        diagnostics = BasicSignalDiagnostics(log_daily_only=False)
        diagnostics.on_after_predict(
            ts_us=1,
            scores={"600000": 0.2},
            requested_count=2,
            skipped_count=1,
        )
    """

    def on_before_predict(
        self,
        *,
        ts_us: int,
        features: np.ndarray,
        model: InferenceModel,
    ) -> None:
        """Observe the raw requested feature matrix.

        Example:
            diagnostics.on_before_predict(
                ts_us=1,
                features=np.array([[1.0]]),
                model=inference_model,
            )
        """
        ...

    def on_after_predict(
        self,
        *,
        ts_us: int,
        scores: Mapping[str, float],
        requested_count: int,
        skipped_count: int,
    ) -> None:
        """Observe requested, scored, and skipped row counts.

        Example:
            diagnostics.on_after_predict(
                ts_us=1,
                scores={"600000": 0.2},
                requested_count=2,
                skipped_count=1,
            )
        """
        ...


@dataclass(slots=True)
class BasicSignalDiagnostics:
    """Log raw missingness and prediction distributions without mutation.

    Example:
        diagnostics = BasicSignalDiagnostics(log_daily_only=False)
        diagnostics.on_after_predict(
            ts_us=1,
            scores={"600000": 0.2},
            requested_count=1,
            skipped_count=0,
        )
    """

    log_daily_only: bool = True
    score_thresholds: tuple[float, ...] = ()
    _last_feature_logged_date: str | None = None
    _last_score_logged_date: str | None = None

    def on_before_predict(
        self,
        *,
        ts_us: int,
        features: np.ndarray,
        model: InferenceModel,
    ) -> None:
        """Log NaN distribution for one requested feature matrix.

        Example:
            diagnostics.on_before_predict(
                ts_us=1,
                features=np.array([[float("nan")]]),
                model=inference_model,
            )
        """

        if not self._should_log(channel="feature", ts_us=ts_us):
            return

        if features.size == 0:
            return

        feature_names = model.feature_names
        X = features
        n_syms, n_feats = X.shape

        mask = np.isnan(X)
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
            f"✅ feature diagnostics; ts_us={ts_us} "
            f"n_sym={n_syms} "
            f"n_feat={n_feats} "
            f"missing_ratio={missing_ratio:.4%} "
            f"sym_missing_min={int(miss_by_sym.min())} "
            f"p50={int(np.percentile(miss_by_sym, 50))} "
            f"p95={int(np.percentile(miss_by_sym, 95))} "
            f"max={int(miss_by_sym.max())} "
            f"top_missing_feats={top_str}"
        )

    def on_after_predict(
        self,
        *,
        ts_us: int,
        scores: Mapping[str, float],
        requested_count: int,
        skipped_count: int,
    ) -> None:
        """Log row counts and the retained finite-score distribution.

        Example:
            diagnostics.on_after_predict(
                ts_us=1,
                scores={"600000": 0.2},
                requested_count=2,
                skipped_count=1,
            )
        """

        if not self._should_log(channel="score", ts_us=ts_us):
            return

        if not scores:
            logs.warning(
                f"⚠️ score diagnostics; reason=no_scores ts_us={ts_us} "
                f"requested={requested_count} scored=0 "
                f"skipped={skipped_count}"
            )
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
            f"✅ score diagnostics; ts_us={ts_us} "
            f"requested={requested_count} "
            f"scored={len(scores)} "
            f"skipped={skipped_count} "
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
