# filepath: src/data_system/steps/fact_materialize_step.py
"""Materialize selected fact sources over resolved trade dates."""

from __future__ import annotations

from src.data_system.context import DataContext
from src.data_system.source_materializer import SourceMaterializer


class FactMaterializeStep:
    """Materialize every formal trade date and reject missing fact data.

    Example:
        step = FactMaterializeStep(materializer=fact_materializer)
        result = step.run(
            DataContext(
                start="2026-07-01",
                end="2026-07-20",
                trade_dates=("2026-07-20",),
            )
        )
    """

    def __init__(self, *, materializer: SourceMaterializer) -> None:
        """Bind the selected fact-source materializer.

        Example:
            step = FactMaterializeStep(materializer=fact_materializer)
        """
        self._materializer = materializer

    def run(self, context: DataContext) -> DataContext:
        """Materialize every trade date or raise after collecting missing dates.

        Example:
            result = step.run(
                DataContext(
                    start="2026-07-01",
                    end="2026-07-20",
                    trade_dates=("2026-07-20",),
                )
            )
        """
        missing_dates: list[str] = []
        for trade_date in context.trade_dates:
            if not self._materializer.materialize(trade_date):
                missing_dates.append(trade_date)

        if missing_dates:
            raise RuntimeError(
                "[OfflineData] fact dates are unavailable "
                f"missing_dates={missing_dates}"
            )
        return context
