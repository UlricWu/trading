# filepath: src/utils/filesystem.py
from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_BINARY_UNIT_BASE = 1024


class FileSystem:
    """Discoverable stateless facade for approved local file operations."""

    @staticmethod
    def ensure_dir(path: str | Path) -> Path:
        """Create a directory without replacing an existing file."""
        directory_path = FileSystem._require_path(path, field_name="path")
        if directory_path.exists() and not directory_path.is_dir():
            raise NotADirectoryError(
                f"path exists but is not a directory: {directory_path}"
            )
        directory_path.mkdir(parents=True, exist_ok=True)
        return directory_path

    @staticmethod
    def file_exists(path: str | Path) -> bool:
        """Return whether `path` exists as a file."""
        return FileSystem._require_path(path, field_name="path").is_file()

    @staticmethod
    def get_file_size(path: str | Path) -> int:
        """Return required-file bytes without conflating absence with emptiness."""
        file_path = FileSystem._require_path(path, field_name="path")
        if not file_path.exists():
            raise FileNotFoundError(f"file does not exist: {file_path}")
        if not file_path.is_file():
            raise IsADirectoryError(f"path is not a file: {file_path}")
        return file_path.stat().st_size

    @staticmethod
    def get_file_size_mib(path: str | Path) -> float:
        """Return file size in mebibytes."""
        return FileSystem.get_file_size(path) / (1024 * 1024)

    @staticmethod
    def format_size(size_bytes: int) -> str:
        """Format a non-negative byte count with binary unit thresholds."""
        if type(size_bytes) is not int:
            raise TypeError("field 'size_bytes' must be an integer")
        if size_bytes < 0:
            raise ValueError("field 'size_bytes' must be non-negative")

        size_value = float(size_bytes)
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if size_value < _BINARY_UNIT_BASE:
                return f"{size_value:.2f} {unit}"
            size_value /= _BINARY_UNIT_BASE
        return f"{size_value:.2f} PiB"

    @staticmethod
    @contextmanager
    def atomic_path(path: str | Path) -> Iterator[Path]:
        """Yield a sibling temporary file and replace `path` on success."""
        target_path = FileSystem._require_path(path, field_name="path")
        FileSystem.ensure_dir(target_path.parent)
        with tempfile.NamedTemporaryFile(
            dir=target_path.parent,
            prefix=f".{target_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

        try:
            yield temporary_path
            if temporary_path.is_symlink() or not temporary_path.is_file():
                raise FileNotFoundError(
                    f"atomic temporary file does not exist: {temporary_path}"
                )
            with temporary_path.open("rb") as temporary_file:
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, target_path)
        except BaseException as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                exc.add_note(
                    f"atomic temporary file cleanup also failed: {cleanup_error!r}"
                )
            raise

    @staticmethod
    def write_bytes_atomic(path: str | Path, data: bytes) -> None:
        """Write bytes through a sibling temporary file and replace `path`."""
        if not isinstance(data, bytes):
            raise TypeError("field 'data' must be bytes")

        with FileSystem.atomic_path(path) as temporary_path:
            temporary_path.write_bytes(data)

    @staticmethod
    def copy_file_atomic(
        source_path: str | Path,
        destination_path: str | Path,
    ) -> Path:
        """
        Copy a file into the target directory and replace `destination_path`.

        This is safe for cross-filesystem moves because the commit step is
        `os.replace()` inside the destination directory.
        """
        source_file = FileSystem._require_path(source_path, field_name="source_path")
        destination_file = FileSystem._require_path(
            destination_path,
            field_name="destination_path",
        )
        if source_file.is_symlink() or not source_file.is_file():
            raise FileNotFoundError(
                f"source must be an existing non-symlink file: {source_file}"
            )

        with FileSystem.atomic_path(destination_file) as temporary_path:
            with (
                source_file.open("rb") as reader,
                temporary_path.open("wb") as writer,
            ):
                shutil.copyfileobj(reader, writer, length=1024 * 1024 * 16)
        return destination_file

    @staticmethod
    def _require_path(value: object, *, field_name: str) -> Path:
        if isinstance(value, Path):
            return value
        if isinstance(value, str):
            if not value:
                raise ValueError(f"field '{field_name}' must be a non-empty path")
            return Path(value)
        raise TypeError(f"field '{field_name}' must be a str or pathlib.Path")

    @staticmethod
    def remove(path: str | Path) -> None:
        """Remove one file or directory; a missing path is already removed."""
        target_path = FileSystem._require_path(path, field_name="path")

        if target_path.is_symlink():
            target_path.unlink()
            return
        if not target_path.exists():
            return
        if target_path.is_dir():
            shutil.rmtree(target_path)
        else:
            target_path.unlink()

    @staticmethod
    def scan_dir(path: str | Path, suffix: str | None = None) -> list[Path]:
        """Return sorted files from one directory level, optionally by suffix."""
        directory_path = FileSystem._require_path(path, field_name="path")
        if suffix is not None and not isinstance(suffix, str):
            raise TypeError("field 'suffix' must be a string or None")
        if suffix is not None and (not suffix or not suffix.startswith(".")):
            raise ValueError("field 'suffix' must start with '.'")
        if not directory_path.exists():
            raise FileNotFoundError(f"directory does not exist: {directory_path}")
        if not directory_path.is_dir():
            raise NotADirectoryError(f"path is not a directory: {directory_path}")

        return sorted(
            file_path
            for file_path in directory_path.iterdir()
            if file_path.is_file() and (suffix is None or file_path.suffix == suffix)
        )

    @staticmethod
    def get_dir_size(path: str | Path) -> int:
        """Return the recursive byte size of files under a directory."""
        total_bytes = 0
        directory_path = FileSystem._require_path(path, field_name="path")

        if not directory_path.exists():
            raise FileNotFoundError(f"directory does not exist: {directory_path}")
        if not directory_path.is_dir():
            raise NotADirectoryError(f"path is not a directory: {directory_path}")

        for file_path in directory_path.rglob("*"):
            if file_path.is_file() and not file_path.is_symlink():
                try:
                    total_bytes += file_path.stat().st_size
                except FileNotFoundError:
                    continue

        return total_bytes

    @staticmethod
    def clean_temp_files(path: str | Path, suffix: str = ".tmp") -> int:
        """Delete matching temporary files recursively and return their count."""
        if not isinstance(suffix, str):
            raise TypeError("field 'suffix' must be a string")
        if not suffix:
            raise ValueError("field 'suffix' must be non-empty")

        directory_path = FileSystem._require_path(path, field_name="path")
        removed_file_count = 0

        if not directory_path.exists():
            return 0
        if not directory_path.is_dir():
            raise NotADirectoryError(f"path is not a directory: {directory_path}")

        for temporary_file in directory_path.rglob("*"):
            if temporary_file.name.endswith(suffix) and (
                temporary_file.is_symlink() or temporary_file.is_file()
            ):
                temporary_file.unlink()
                removed_file_count += 1

        return removed_file_count


__all__ = ["FileSystem"]
