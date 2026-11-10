# filepath: src/training/steps/artifact_persist.py
"""Deliver each trained window before any evaluation or reporting."""

from src import logs
from src.training.artifact import save
from src.training.context import TrainingContext


class ArtifactPersistStep:
    """Publish the current window's immutable ready model.

    Example:
        context = ArtifactPersistStep().run(trained_context)
    """

    def run(self, context: TrainingContext) -> TrainingContext:
        """Save this model without requiring predictions, metrics, or a report.

        Example:
            context = ArtifactPersistStep().run(trained_context)
        """
        if context.model is None:
            raise RuntimeError("ArtifactPersistStep requires an inference model")
        save(context.window.model_ref, context.model)
        logs.info(f"✅ 模型写入; model_ref={context.window.model_ref}")
        return context
