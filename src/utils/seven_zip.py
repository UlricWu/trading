# filepath: src/utils/seven_zip.py
"""Launch 7z-compatible extraction without shell command construction."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final, Protocol


class _PopenFactory(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        stdout: int,
        stderr: int,
    ) -> subprocess.Popen[bytes]: ...


SEVEN_ZIP_CANDIDATES: Final[tuple[str, ...]] = ("7zz", "7za", "7z")


def resolve_7z_executable(
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Return the first installed executable in the documented preference order."""
    if not callable(which):
        raise TypeError("field 'which' must be callable")

    for executable in SEVEN_ZIP_CANDIDATES:
        resolved_executable = which(executable)
        if resolved_executable is not None:
            if not isinstance(resolved_executable, str) or not resolved_executable:
                raise TypeError("which must return a non-empty string or None")
            return resolved_executable

    candidates = ", ".join(SEVEN_ZIP_CANDIDATES)
    raise RuntimeError(
        "7z-compatible CLI not found; "
        f"install one of [{candidates}] and ensure it is on PATH"
    )


def open_extract_stdout(
    archive_path: str | Path,
    *,
    which: Callable[[str], str | None] = shutil.which,
    popen: _PopenFactory = subprocess.Popen,
) -> subprocess.Popen[bytes]:
    """Start one ``7z x -so`` process with byte stdout and discarded stderr."""
    if not isinstance(archive_path, (str, Path)):
        raise TypeError("field 'archive_path' must be a str or pathlib.Path")
    if not callable(popen):
        raise TypeError("field 'popen' must be callable")

    archive = Path(archive_path)
    if not archive.is_file():
        raise FileNotFoundError(f"archive file does not exist: {archive}")

    executable = resolve_7z_executable(which=which)
    return popen(
        [executable, "x", "-so", str(archive)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


__all__ = ["SEVEN_ZIP_CANDIDATES", "open_extract_stdout", "resolve_7z_executable"]
