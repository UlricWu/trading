# filepath: tests/utils/test_seven_zip.py
from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from src.utils.seven_zip import open_extract_stdout, resolve_7z_executable


def test_resolve_7z_executable_uses_documented_preference_order() -> None:
    candidates_checked: list[str] = []

    def which(executable: str) -> str | None:
        candidates_checked.append(executable)
        return "/usr/bin/7za" if executable == "7za" else None

    assert resolve_7z_executable(which=which) == "/usr/bin/7za"
    assert candidates_checked == ["7zz", "7za"]


def test_open_extract_stdout_passes_an_argument_vector(tmp_path: Path) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    recorded_args: list[str] = []

    def popen(
        args: Sequence[str],
        *,
        stdout: int,
        stderr: int,
    ) -> subprocess.Popen[bytes]:
        recorded_args.extend(args)
        return subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=stdout,
            stderr=stderr,
        )

    process = open_extract_stdout(
        archive_path,
        which=lambda _executable: "/usr/bin/7zz",
        popen=popen,
    )
    process.communicate()

    assert recorded_args == ["/usr/bin/7zz", "x", "-so", str(archive_path)]
