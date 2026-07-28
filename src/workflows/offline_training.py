# filepath: src/workflows/offline_training.py
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from src.access import Access
from src.config.model_config import ModelConfig
from src.data_system.builders.registry import get_label_builder
from src.observability.instrumentation import Instrumentation
from src.pipeline import run_steps
from src.training.context import TrainingContext
from src.training.steps.artifact_persist_step import ArtifactPersistStep
from src.training.steps.dataset_build_step import DatasetBuildStep
from src.training.steps.model_evaluate_step import ICEvaluateStep
from src.training.steps.model_train_step import ModelTrainStep
from src.training.steps.preprocess_step import PreprocessStep
from src.training.steps.report_step import ReportStep
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager


MODEL_GROUP = "sgd_regression"


@dataclass(frozen=True, slots=True)
class TrainingScheduleEntry:
    """Identify one training window and its single evaluation date.

    Example:
        entry = TrainingScheduleEntry(
            train_start_date="2026-07-01",
            train_end_date="2026-07-10",
            eval_date="2026-07-11",
        )
    """

    train_start_date: str
    train_end_date: str
    eval_date: str


class TrainingCalendar(Protocol):
    """Provide available training dates for one requested range.

    Example:
        dates = calendar(
            pm=path_manager,
            start_date="2026-07-01",
            end_date="2026-07-20",
        )
    """

    def __call__(
        self,
        *,
        pm: PathManager,
        start_date: str,
        end_date: str,
    ) -> Sequence[str]:
        """Return ordered open dates in the requested range.

        Example:
            dates = calendar(
                pm=path_manager,
                start_date="2026-07-01",
                end_date="2026-07-20",
            )
        """
        ...


def build_training_experiment_name(
    *,
    start_date: str,
    end_date: str,
    experiment_id: str,
) -> str:
    """Return the training artifact namespace for one accepted range.

    Example:
        name = build_training_experiment_name(
            start_date="2026-07-01",
            end_date="2026-07-20",
            experiment_id="run-1",
        )
    """
    return f"training_{start_date}_{end_date}_{experiment_id}"


def run_offline_training(
    *,
    model_config: ModelConfig,
    path_manager: PathManager,
    experiment_id: str,
    start_date: str,
    end_date: str,
) -> None:
    """Run one model CLI range through the offline training flow.

    The workflow boundary owns execution identity, schedule expansion, context
    creation, and step graph execution.

    Example:
        run_offline_training(
            model_config=model_config,
            path_manager=path_manager,
            experiment_id="run-1",
            start_date="2026-07-01",
            end_date="2026-07-20",
        )
    """
    experiment_name = build_training_experiment_name(
        start_date=start_date,
        end_date=end_date,
        experiment_id=experiment_id,
    )
    experiment_dir = path_manager.experiment_dir(experiment_name=experiment_name)
    if experiment_dir.exists():
        raise FileExistsError(f"experiment already exists: {experiment_name}")

    label_builder = get_label_builder(
        model_config.dataset.label_set,
        model_config.dataset.label_version,
    )
    label_eval_offset = label_builder.target_lookahead(
        model_config.dataset.label_column
    )
    schedule = build_training_schedule(
        path_manager=path_manager,
        start_date=start_date,
        end_date=end_date,
        train_window_days=model_config.train_window_days,
        eval_offset=label_eval_offset,
    )

    training_context = TrainingContext(
        pm=path_manager,
        experiment_name=experiment_name,
    )

    daily_steps: tuple[Callable[[TrainingContext], None], ...] = (
        DatasetBuildStep(model_config.dataset),
        PreprocessStep(model_config.preprocessing),
        ModelTrainStep(
            group=MODEL_GROUP,
            model_params=model_config.model_params,
        ),
        ICEvaluateStep(),
    )
    final_steps: tuple[Callable[[TrainingContext], None], ...] = (
        ArtifactPersistStep(
            experiment_id=experiment_id,
            model_group=MODEL_GROUP,
            dataset_cfg=model_config.dataset,
            label_lookahead=label_eval_offset,
        ),
        ReportStep(),
    )

    with Instrumentation(experiment_name) as instrumentation:
        for entry in schedule:
            training_context.train_start_date = entry.train_start_date
            training_context.train_end_date = entry.train_end_date
            training_context.eval_date = entry.eval_date
            run_steps(training_context, daily_steps, instrumentation)
        run_steps(training_context, final_steps, instrumentation)


def build_training_schedule(
    *,
    path_manager: PathManager,
    start_date: str,
    end_date: str,
    train_window_days: int,
    eval_offset: int,
    calendar_fn: TrainingCalendar | None = None,
) -> list[TrainingScheduleEntry]:
    """Build concrete schedule entries from the job range and tradable dates.

    `train_window_days=0` means expanding. `train_window_days=1` means one
    tradable training day. `train_window_days=N` means a fixed rolling train
    window of N tradable days.

    Example:
        schedule = build_training_schedule(
            path_manager=path_manager,
            start_date="2026-07-01",
            end_date="2026-07-20",
            train_window_days=5,
            eval_offset=1,
        )
    """
    start_date = DateTimeUtils.require_system_date(start_date, field_name="start_date")
    end_date = DateTimeUtils.require_system_date(end_date, field_name="end_date")
    if start_date > end_date:
        raise ValueError(f"invalid date range: start={start_date}, end={end_date}")
    if not isinstance(train_window_days, int) or isinstance(train_window_days, bool):
        raise TypeError("train_window_days must be an int")
    if not isinstance(eval_offset, int) or isinstance(eval_offset, bool):
        raise TypeError("eval_offset must be an int")
    if train_window_days < 0:
        raise ValueError("train_window_days must be >= 0")
    if eval_offset < 0:
        raise ValueError("eval_offset must be >= 0")

    raw_open_dates: Sequence[str]
    if calendar_fn is None:
        raw_open_dates = Access(
            pm=path_manager,
            processed_version="v1",
        ).trade_dates(
            start_date=start_date,
            end_date=end_date,
        )
    else:
        raw_open_dates = calendar_fn(
            start_date=start_date,
            end_date=end_date,
            pm=path_manager,
        )
    open_dates = _validated_open_dates(
        raw_open_dates,
        start_date=start_date,
        end_date=end_date,
    )
    schedule_entries: list[TrainingScheduleEntry] = []

    for train_end_index, train_end_date in enumerate(open_dates):
        eval_index = train_end_index + eval_offset
        if eval_index >= len(open_dates):
            continue

        if train_window_days == 0:
            train_start_index = 0
        else:
            train_start_index = train_end_index - train_window_days + 1
            if train_start_index < 0:
                continue

        schedule_entries.append(
            TrainingScheduleEntry(
                train_start_date=open_dates[train_start_index],
                train_end_date=train_end_date,
                eval_date=open_dates[eval_index],
            )
        )

    if not schedule_entries:
        raise ValueError(
            "[TrainingSchedule] empty schedule; check date range, "
            "train_window_days, and eval_offset"
        )
    return schedule_entries


def _validated_open_dates(
    raw_dates: Sequence[str],
    *,
    start_date: str,
    end_date: str,
) -> list[str]:
    open_dates: list[str] = []
    previous: str | None = None
    seen: set[str] = set()
    for raw_date in raw_dates:
        current = DateTimeUtils.require_system_date(raw_date, field_name="open_date")
        if current < start_date or current > end_date:
            raise ValueError(
                f"open_date out of requested range: {current} "
                f"range={start_date}..{end_date}"
            )
        if current in seen:
            raise ValueError(f"duplicate open_date: {current}")
        if previous is not None and current < previous:
            raise ValueError("open_dates must be sorted ascending")
        seen.add(current)
        open_dates.append(current)
        previous = current
    return open_dates
