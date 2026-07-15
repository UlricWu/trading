# filepath: src/utils/filesystem.py
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

_BINARY_UNIT_BASE = 1024


class FileSystem:
    """Stateless local-filesystem operations over caller-provided paths."""

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
    def files_have_same_nonzero_size(
        left_path: str | Path,
        right_path: str | Path,
    ) -> bool:
        """Compare non-empty files by size only, not by content hash."""
        left_file = FileSystem._require_path(left_path, field_name="left_path")
        right_file = FileSystem._require_path(right_path, field_name="right_path")
        if not (left_file.is_file() and right_file.is_file()):
            return False

        left_size_bytes = left_file.stat().st_size
        if left_size_bytes == 0:
            return False
        return left_size_bytes == right_file.stat().st_size

    @staticmethod
    def format_size(size_bytes: int) -> str:
        """Format a non-negative byte count with binary unit thresholds."""
        if type(size_bytes) is not int:
            raise TypeError("field 'size_bytes' must be an integer")
        if size_bytes < 0:
            raise ValueError("field 'size_bytes' must be non-negative")

        size_value = float(size_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size_value < _BINARY_UNIT_BASE:
                return f"{size_value:.2f} {unit}"
            size_value /= _BINARY_UNIT_BASE
        return f"{size_value:.2f} PB"

    @staticmethod
    def safe_write(path: str | Path, payload: bytes) -> None:
        """Fsync a temporary file before atomically replacing the target."""
        if not isinstance(payload, bytes):
            raise TypeError("field 'payload' must be bytes")

        target_path = FileSystem._require_path(path, field_name="path")
        FileSystem.ensure_dir(target_path.parent)

        temporary_path = FileSystem._unique_tmp_path(target_path)

        published = False
        try:
            with temporary_path.open("wb") as temporary_file:
                temporary_file.write(payload)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            FileSystem.publish_file_atomic(temporary_path, target_path)
            published = True
        finally:
            if not published:
                temporary_path.unlink(missing_ok=True)

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
        if not source_file.is_file():
            raise FileNotFoundError(f"source file does not exist: {source_file}")

        FileSystem.ensure_dir(destination_file.parent)
        temporary_path = FileSystem._unique_tmp_path(destination_file)

        published = False
        try:
            with (
                source_file.open("rb") as reader,
                temporary_path.open("wb") as writer,
            ):
                shutil.copyfileobj(reader, writer, length=1024 * 1024 * 16)
                writer.flush()
                os.fsync(writer.fileno())

            FileSystem.publish_file_atomic(temporary_path, destination_file)
            published = True
            return destination_file
        finally:
            if not published:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _unique_tmp_path(path: Path) -> Path:
        return path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")

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
    def publish_file_atomic(
        staged_path: str | Path,
        destination_path: str | Path,
    ) -> Path:
        """Consume a staged file and atomically replace a sibling destination.

        Both paths must resolve to the same parent directory so the final
        ``os.replace`` cannot cross filesystem boundaries.
        """
        staged_file = FileSystem._require_path(staged_path, field_name="staged_path")
        destination_file = FileSystem._require_path(
            destination_path,
            field_name="destination_path",
        )
        if staged_file.is_symlink() or not staged_file.is_file():
            raise FileNotFoundError(f"staged file does not exist: {staged_file}")
        if staged_file.resolve() == destination_file.resolve():
            raise ValueError("staged_path and destination_path must be different files")

        destination_directory = FileSystem.ensure_dir(destination_file.parent)
        if staged_file.parent.resolve() != destination_directory.resolve():
            raise ValueError(
                "staged_path and destination_path must share a parent directory"
            )

        os.replace(staged_file, destination_file)
        FileSystem._fsync_dir(destination_directory)
        return destination_file

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        if os.name == "nt":
            return
        open_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(path, open_flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

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
                total_bytes += file_path.stat().st_size

        return total_bytes

    @staticmethod
    def clean_temp_files(path: str | Path, suffix: str = ".tmp") -> int:
        """Delete matching temporary files recursively and return their count."""
        if not isinstance(suffix, str):
            raise TypeError("field 'suffix' must be a string")
        if not suffix or "/" in suffix or "\\" in suffix:
            raise ValueError("field 'suffix' must be a non-empty filename suffix")

        directory_path = FileSystem._require_path(path, field_name="path")
        removed_file_count = 0

        if not directory_path.exists():
            return 0
        if not directory_path.is_dir():
            raise NotADirectoryError(f"path is not a directory: {directory_path}")

        for temporary_file in directory_path.rglob("*"):
            if temporary_file.is_file() and temporary_file.name.endswith(suffix):
                temporary_file.unlink()
                removed_file_count += 1

        return removed_file_count


__all__ = ["FileSystem"]
