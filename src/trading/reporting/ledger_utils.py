# filepath: src/trading/reporting/ledger_utils.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pandas as pd


def flatten_ledger_records(
    records: Sequence[Mapping[str, object]],
) -> pd.DataFrame:
    """
    Flatten ledger records into a DataFrame.

    Rules:
    - ledger.records may contain a 'meta' dict column
    - we normalize meta into top-level columns (same behavior as CSV export)
    """
    df = pd.DataFrame(list(records))
    if df.empty:
        return df

    if "meta" in df.columns:
        meta_df = pd.json_normalize(df["meta"].fillna({}))
        meta_df.columns = [c.replace("meta.", "") for c in meta_df.columns]
        df = pd.concat([df.drop(columns=["meta"]), meta_df], axis=1)

    return df


def fills(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "event" not in df.columns:
        return df.iloc[0:0].copy()
    return df[df["event"] == "FILL"].copy()


@dataclass(frozen=True, slots=True)
class SlippageStats:
    max_abs_bp: float
    mean_abs_bp: float
    nonzero_count: int
    fill_price_le_0: int


def compute_slippage_stats(fill_df: pd.DataFrame) -> SlippageStats:
    """
    Derive slippage bp from slippage_cost / notional.

    Requires:
    - qty, price
    - slippage_cost (expanded from meta if needed)
    """
    if fill_df.empty:
        return SlippageStats(0.0, 0.0, 0, 0)

    working = fill_df.copy()
    for c in ["qty", "price", "slippage_cost"]:
        if c in working.columns:
            working[c] = pd.to_numeric(working[c], errors="coerce")

    fill_price_le_0 = int((working["price"] <= 0).sum()) if "price" in working.columns else 0

    if not {"qty", "price", "slippage_cost"}.issubset(working.columns):
        return SlippageStats(0.0, 0.0, 0, fill_price_le_0)

    notional = working["qty"] * working["price"]
    valid = (notional > 0) & working["slippage_cost"].notna()

    if not valid.any():
        return SlippageStats(0.0, 0.0, 0, fill_price_le_0)

    slip_bp = (working.loc[valid, "slippage_cost"] / notional.loc[valid]) * 1e4
    slip_bp = slip_bp.dropna()

    if slip_bp.empty:
        return SlippageStats(0.0, 0.0, 0, fill_price_le_0)

    max_abs = float(slip_bp.abs().max())
    mean_abs = float(slip_bp.abs().mean())
    nonzero = int((slip_bp.abs() > 1e-12).sum())
    return SlippageStats(max_abs, mean_abs, nonzero, fill_price_le_0)


@dataclass(frozen=True, slots=True)
class TradingFacts:
    buy_fills: int
    sell_fills: int
    gross_buy: float
    gross_sell: float


def trading_facts(fill_df: pd.DataFrame) -> TradingFacts:
    """
    Returns:
        buy_fills, sell_fills, gross_buy, gross_sell
    """
    if fill_df.empty:
        return TradingFacts(0, 0, 0.0, 0.0)

    buy_fills = int((fill_df.get("side") == "BUY").sum()) if "side" in fill_df.columns else 0
    sell_fills = int((fill_df.get("side") == "SELL").sum()) if "side" in fill_df.columns else 0

    gross_buy = 0.0
    gross_sell = 0.0
    if {"qty", "price", "side"}.issubset(fill_df.columns):
        q = pd.to_numeric(fill_df["qty"], errors="coerce")
        p = pd.to_numeric(fill_df["price"], errors="coerce")
        amt = q * p
        gross_buy = float(amt[fill_df["side"] == "BUY"].sum())
        gross_sell = float(amt[fill_df["side"] == "SELL"].sum())

    return TradingFacts(buy_fills, sell_fills, gross_buy, gross_sell)
