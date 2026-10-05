# filepath: src/training/artifact.py
"""Save and restore one immutable ready model by its absolute file reference."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import joblib

from src.training.inference_model import InferenceModel


def save(ref: Path, model: InferenceModel) -> None:
    """Publish a complete single-file model without replacing an existing ref.

    Example:
        save(Path("/data/trading/models/run-001.joblib"), inference_model)
    """
    if not ref.is_absolute():
        raise ValueError("model ref must be an absolute Path")
    ref.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=ref.parent, prefix=f".{ref.name}.")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            joblib.dump(model, stream)
        os.link(temporary, ref)
    except BaseException as error:
        try:
            temporary.unlink()
        except OSError as cleanup_error:
            error.add_note(f"model temporary cleanup failed: {cleanup_error}")
        raise
    temporary.unlink()


def load(ref: Path) -> InferenceModel:
    """Restore the exact ready object; propagate I/O and restoration errors.

    Example:
        model = load(Path("/data/trading/models/run-001.joblib"))
        predictions = model.predict(np.array([[0.0], [1.0]]))
    """
    if not ref.is_absolute():
        raise ValueError("model ref must be an absolute Path")
    restored: object = joblib.load(ref)
    if not isinstance(restored, InferenceModel):
        raise TypeError("model file must contain an InferenceModel")
    return restored
