# filepath: src/pipeline/phase.py
from __future__ import annotations

import pandas as pd

from enum import IntEnum


class MarketPhase(IntEnum):
    AUCTION = 0
    BREAK = 1
    TRADING = 2


AUCTION = MarketPhase.AUCTION
BREAK = MarketPhase.BREAK
TRADING = MarketPhase.TRADING


def is_trainable_row(df: pd.DataFrame) -> pd.Series:
    """Return the training-eligibility mask for a feature frame.

    A row is trainable iff:
    - phase == TRADING

    The rule defines the statistical sample boundary, so changing it
    invalidates experiments built under the previous rule.
    """
    if "phase" not in df.columns:
        raise RuntimeError(
            "[SampleRule] missing required column: phase"
        )

    return df["phase"] == TRADING
