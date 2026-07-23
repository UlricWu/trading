# filepath: src/data_system/pipeline.py
from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from src import logs
from src.data_system.context import DataContext
from src.observability.instrumentation import Instrumentation
from src.pipeline.step import PipelineStep
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager


class DataRunStatus(StrEnum):
    """Public outcome of one offline data run."""

    SUCCESS = "success"
    SKIPPED = "skipped"


class DataPipeline:
    """Execute one prebuilt offline data step sequence."""

    def __init__(
        self,
        steps: Sequence[PipelineStep[DataContext]],
        pm: PathManager,
        inst: Instrumentation,
    ) -> None:
        self.steps = tuple(steps)
        self.pm = pm
        self.inst = inst

    def run(self, trade_date: str) -> DataRunStatus:
        """Run one date and return its public completion status."""
        trade_date = DateTimeUtils.require_system_date(
            trade_date,
            field_name="trade_date",
        )
        logs.info(f"[DataPipeline] started trade_date={trade_date}")

        ctx = DataContext(
            trade_date=trade_date,
            pm=self.pm,
        )

        for index, step in enumerate(self.steps):
            with self.inst.timer(step.__class__.__name__):
                next_ctx = step.run(ctx)

            if next_ctx is None:
                if index != 0:
                    raise RuntimeError(
                        f"{step.__class__.__name__} returned no context"
                    )
                logs.warning(
                    f"[DataPipeline] skipped trade_date={trade_date} "
                    "reason=no_source_payload"
                )
                self.inst.generate_timeline_report(trade_date)
                return DataRunStatus.SKIPPED

            ctx = next_ctx

        self.inst.generate_timeline_report(trade_date)
        logs.info(f"[DataPipeline] finished trade_date={trade_date}")
        return DataRunStatus.SUCCESS
