# filepath: tests/trading/backtest/test_evaluation.py
"""Raw-close diagnostics retain Rank IC undefined reasons and coverage."""

from src.trading.backtest.evaluation import signal_eval_frame, summarize_signal_eval_frames


def test_raw_close_diagnostics_keep_undefined_dates_and_all_reasons() -> None:
    empty = signal_eval_frame(scores={}, entry_prices={}, exit_prices={})
    singleton = signal_eval_frame(
        scores={"000001": 0.0}, entry_prices={"000001": 10.0}, exit_prices={"000001": 11.0},
    )
    defined = signal_eval_frame(
        scores={"000001": 0.0, "600000": 1.0},
        entry_prices={"000001": 10.0, "600000": 10.0},
        exit_prices={"000001": 10.0, "600000": 11.0},
    )
    assert empty["rank_ic"] is None
    assert empty["rank_ic_pair_count"] == 0
    assert empty["rank_ic_undefined_reasons"] == ("insufficient_pairs",)
    assert singleton["rank_ic"] is None
    assert singleton["rank_ic_undefined_reasons"] == (
        "insufficient_pairs", "constant_prediction", "constant_label",
    )
    assert defined["rank_ic"] == 1.0
    summary = summarize_signal_eval_frames([empty, singleton, defined])
    assert summary["frames"] == 3
    assert summary["rank_ic_defined_date_count"] == 1
    assert summary["mean_rank_ic"] == 1.0
    assert summary["rank_ic_undefined_reason_counts"] == {
        "insufficient_pairs": 2, "constant_prediction": 1, "constant_label": 1,
    }
