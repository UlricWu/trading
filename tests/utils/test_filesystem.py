# filepath: tests/utils/test_filesystem.py
from __future__ import annotations

from pathlib import Path

import pytest

import src.utils.filesystem as filesystem_module
from src.utils.filesystem import FileSystem


def test_ensure_dir_creates_parents_and_rejects_existing_file(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "parent" / "child"

    assert FileSystem.ensure_dir(directory) == directory
    assert directory.is_dir()

    occupied_path = tmp_path / "occupied"
    occupied_path.write_bytes(b"data")
    with pytest.raises(NotADirectoryError):
        FileSystem.ensure_dir(occupied_path)


def test_file_queries_distinguish_file_directory_missing_and_empty(
    tmp_path: Path,
) -> None:
    empty_file = tmp_path / "empty.bin"
    empty_file.touch()

    assert FileSystem.file_exists(empty_file)
    assert not FileSystem.file_exists(tmp_path)
    assert not FileSystem.file_exists(tmp_path / "missing.bin")
    assert FileSystem.get_file_size(empty_file) == 0
    assert FileSystem.get_file_size_mib(empty_file) == 0.0

    with pytest.raises(FileNotFoundError, match="does not exist"):
        FileSystem.get_file_size(tmp_path / "missing.bin")
    with pytest.raises(IsADirectoryError, match="not a file"):
        FileSystem.get_file_size(tmp_path)


def test_format_size_uses_explicit_binary_units() -> None:
    assert FileSystem.format_size(0) == "0.00 B"
    assert FileSystem.format_size(1024) == "1.00 KiB"
    assert FileSystem.format_size(1024**2) == "1.00 MiB"
    assert FileSystem.format_size(1024**5) == "1.00 PiB"

    with pytest.raises(TypeError, match="integer"):
        FileSystem.format_size(True)
    with pytest.raises(ValueError, match="non-negative"):
        FileSystem.format_size(-1)


def test_atomic_path_uses_unique_sibling_files_and_publishes(
    tmp_path: Path,
) -> None:
    first_destination = tmp_path / "first.bin"
    second_destination = tmp_path / "second.bin"

    with FileSystem.atomic_path(first_destination) as first_temporary:
        with FileSystem.atomic_path(second_destination) as second_temporary:
            assert first_temporary.parent == tmp_path
            assert second_temporary.parent == tmp_path
            assert first_temporary != second_temporary
            first_temporary.write_bytes(b"first")
            second_temporary.write_bytes(b"second")

    assert first_destination.read_bytes() == b"first"
    assert second_destination.read_bytes() == b"second"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_atomic_path_preserves_destination_when_writer_fails(tmp_path: Path) -> None:
    destination = tmp_path / "payload.bin"
    destination.write_bytes(b"old")

    with pytest.raises(RuntimeError, match="writer failed"):
        with FileSystem.atomic_path(destination) as temporary:
            temporary.write_bytes(b"new")
            raise RuntimeError("writer failed")

    assert destination.read_bytes() == b"old"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_atomic_path_preserves_destination_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "payload.bin"
    destination.write_bytes(b"old")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(filesystem_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        with FileSystem.atomic_path(destination) as temporary:
            temporary.write_bytes(b"new")

    assert destination.read_bytes() == b"old"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_write_bytes_atomic_creates_parent_and_replaces_file(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "payload.bin"

    FileSystem.write_bytes_atomic(destination, b"first")
    FileSystem.write_bytes_atomic(destination, b"second")

    assert destination.read_bytes() == b"second"
    assert list(destination.parent.glob(".*.tmp")) == []


def test_copy_file_atomic_copies_content_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source payload")
    destination = tmp_path / "nested" / "destination.bin"
    destination.parent.mkdir()
    destination.write_bytes(b"old")

    copied_path = FileSystem.copy_file_atomic(source, destination)

    assert copied_path == destination
    assert source.read_bytes() == b"source payload"
    assert destination.read_bytes() == b"source payload"
    assert list(destination.parent.glob(".*.tmp")) == []


def test_copy_file_atomic_rejects_symlink_source(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    source_link = tmp_path / "source-link.bin"
    source_link.symlink_to(source)

    with pytest.raises(FileNotFoundError, match="non-symlink"):
        FileSystem.copy_file_atomic(source_link, tmp_path / "destination.bin")


def test_remove_handles_missing_file_directory_and_symlink(tmp_path: Path) -> None:
    FileSystem.remove(tmp_path / "missing")

    file_path = tmp_path / "file.bin"
    file_path.write_bytes(b"data")
    FileSystem.remove(file_path)
    assert not file_path.exists()

    target_directory = tmp_path / "target"
    target_directory.mkdir()
    kept_file = target_directory / "kept.txt"
    kept_file.write_text("kept", encoding="utf-8")
    link_path = tmp_path / "target-link"
    link_path.symlink_to(target_directory, target_is_directory=True)
    FileSystem.remove(link_path)
    assert not link_path.exists()
    assert kept_file.is_file()

    FileSystem.remove(target_directory)
    assert not target_directory.exists()


def test_scan_dir_is_sorted_non_recursive_and_requires_valid_suffix(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "b.parquet").touch()
    (tmp_path / "a.parquet").touch()
    (tmp_path / "ignored.txt").touch()
    (nested / "nested.parquet").touch()

    assert FileSystem.scan_dir(tmp_path, suffix=".parquet") == [
        tmp_path / "a.parquet",
        tmp_path / "b.parquet",
    ]

    with pytest.raises(ValueError, match="start with"):
        FileSystem.scan_dir(tmp_path, suffix="parquet")
    with pytest.raises(FileNotFoundError):
        FileSystem.scan_dir(tmp_path / "missing")


def test_get_dir_size_counts_regular_files_without_following_symlinks(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "first.bin").write_bytes(b"abc")
    target = nested / "second.bin"
    target.write_bytes(b"de")
    (tmp_path / "second-link.bin").symlink_to(target)

    assert FileSystem.get_dir_size(tmp_path) == 5
    with pytest.raises(FileNotFoundError):
        FileSystem.get_dir_size(tmp_path / "missing")


def test_clean_temp_files_deletes_matching_files_and_symlinks(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    temporary_file = nested / "payload.tmp"
    temporary_file.write_text("remove", encoding="utf-8")
    kept_file = nested / "payload.txt"
    kept_file.write_text("keep", encoding="utf-8")
    temporary_link = tmp_path / "missing.tmp"
    temporary_link.symlink_to(tmp_path / "missing-target")
    kept_directory = tmp_path / "kept.tmp"
    kept_directory.mkdir()

    assert FileSystem.clean_temp_files(tmp_path) == 2
    assert kept_file.is_file()
    assert kept_directory.is_dir()
    assert not temporary_link.is_symlink()

    assert FileSystem.clean_temp_files(tmp_path / "missing") == 0
    with pytest.raises(ValueError, match="non-empty"):
        FileSystem.clean_temp_files(tmp_path, suffix="")
