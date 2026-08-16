# filepath: tests/utils/test_price_utils.py
from __future__ import annotations

import pandas as pd
import pytest

from src.utils.price_utils import apply_asof_price_adjustment


def test_apply_asof_price_adjustment_qfq_uses_factor_over_asof_factor() -> None:
    df = pd.DataFrame(
        {
            "symbol": ["000001", "000001"],
            "trade_date": ["2026-01-01", "2026-01-02"],
            "close": [20.0, 10.0],
            "adj_factor": [1.0, 2.0],
        }
    )

    out = apply_asof_price_adjustment(
        df,
        adjustment="qfq",
        asof_date="2026-01-02",
        price_columns=("close",),
        output_prefix="qfq_",
    )

    assert out["close"].tolist() == [20.0, 10.0]
    assert out["qfq_close"].tolist() == pytest.approx([10.0, 10.0])


def test_apply_asof_price_adjustment_hfq_uses_factor_directly() -> None:
    df = pd.DataFrame(
        {
            "symbol": ["000001", "000001"],
            "trade_date": ["2026-01-01", "2026-01-02"],
            "close": [20.0, 10.0],
            "adj_factor": [1.0, 2.0],
        }
    )

    out = apply_asof_price_adjustment(
        df,
        adjustment="hfq",
        asof_date="2026-01-02",
        price_columns=("close",),
    )

    assert out["close"].tolist() == pytest.approx([20.0, 20.0])


def test_apply_asof_price_adjustment_invalid_factor_outputs_null() -> None:
    df = pd.DataFrame(
        {
            "symbol": ["000001", "000001"],
            "trade_date": ["2026-01-01", "2026-01-02"],
            "close": [20.0, 10.0],
            "adj_factor": [0.0, 2.0],
        }
    )

    out = apply_asof_price_adjustment(
        df,
        adjustment="qfq",
        asof_date="2026-01-02",
        price_columns=("close",),
        output_prefix="qfq_",
    )

    assert pd.isna(out["qfq_close"].iloc[0])
    assert out["qfq_close"].iloc[1] == pytest.approx(10.0)


def test_apply_asof_price_adjustment_requires_requested_price_columns() -> None:
    frame = pd.DataFrame({"close": [10.0]})

    with pytest.raises(
        ValueError,
        match=r"price adjustment: columns must exist exactly once: \['open'\]",
    ):
        apply_asof_price_adjustment(
            frame,
            adjustment="raw",
            asof_date="2026-01-02",
            price_columns=("open", "close"),
        )


def test_apply_asof_price_adjustment_validates_asof_date_for_every_mode() -> None:
    frame = pd.DataFrame({"close": [10.0]})

    with pytest.raises(ValueError, match="asof_date"):
        apply_asof_price_adjustment(
            frame,
            adjustment="raw",
            asof_date="2026-02-30",
            price_columns=("close",),
        )


def test_apply_asof_price_adjustment_accepts_empty_qfq_frame() -> None:
    frame = pd.DataFrame(columns=["symbol", "trade_date", "close", "adj_factor"])

    adjusted = apply_asof_price_adjustment(
        frame,
        adjustment="qfq",
        asof_date="2026-01-02",
        price_columns=("close",),
    )

    assert adjusted.empty
    assert adjusted.columns.tolist() == frame.columns.tolist()


def test_apply_asof_price_adjustment_requires_every_qfq_anchor() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["000001", "000002"],
            "trade_date": ["2026-01-02", "2026-01-01"],
            "close": [10.0, 20.0],
            "adj_factor": [2.0, 1.0],
        }
    )

    with pytest.raises(ValueError, match=r"missing symbols.*000002"):
        apply_asof_price_adjustment(
            frame,
            adjustment="qfq",
            asof_date="2026-01-02",
            price_columns=("close",),
        )


@pytest.mark.parametrize(
    "trade_date",
    [
        20260102,
        None,
        "2026-01-02 ",
        "2026-02-30",
    ],
)
def test_apply_asof_price_adjustment_validates_qfq_trade_date_column(
    trade_date: object,
) -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["000001"],
            "trade_date": [trade_date],
            "close": [10.0],
            "adj_factor": [2.0],
        }
    )

    with pytest.raises(ValueError, match="trade_date"):
        apply_asof_price_adjustment(
            frame,
            adjustment="qfq",
            asof_date="2026-01-02",
            price_columns=("close",),
        )
