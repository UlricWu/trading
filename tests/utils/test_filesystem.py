# filepath: tests/utils/test_filesystem.py
from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.filesystem import FileSystem


def test_required_size_queries_distinguish_missing_and_empty_files(
    tmp_path: Path,
) -> None:
    empty_file = tmp_path / "empty.bin"
    empty_file.touch()

    assert FileSystem.get_file_size(empty_file) == 0
    assert FileSystem.get_file_size_mib(empty_file) == 0.0
    with pytest.raises(FileNotFoundError, match="does not exist"):
        FileSystem.get_file_size(tmp_path / "missing.bin")


def test_same_nonzero_size_predicate_states_its_limited_contract(
    tmp_path: Path,
) -> None:
    left_file = tmp_path / "left.bin"
    right_file = tmp_path / "right.bin"
    left_file.write_bytes(b"abc")
    right_file.write_bytes(b"xyz")

    assert FileSystem.files_have_same_nonzero_size(left_file, right_file)


def test_publish_file_atomic_consumes_sibling_stage(tmp_path: Path) -> None:
    staged_path = tmp_path / ".payload.stage"
    destination_path = tmp_path / "payload.bin"
    staged_path.write_bytes(b"new")
    destination_path.write_bytes(b"old")

    published_path = FileSystem.publish_file_atomic(staged_path, destination_path)

    assert published_path == destination_path
    assert destination_path.read_bytes() == b"new"
    assert not staged_path.exists()


def test_publish_file_atomic_rejects_cross_directory_stage(tmp_path: Path) -> None:
    source_directory = tmp_path / "source"
    destination_directory = tmp_path / "destination"
    source_directory.mkdir()
    destination_directory.mkdir()
    staged_path = source_directory / "payload.stage"
    staged_path.write_bytes(b"data")

    with pytest.raises(ValueError, match="share a parent"):
        FileSystem.publish_file_atomic(
            staged_path,
            destination_directory / "payload.bin",
        )

    assert staged_path.exists()


def test_publish_file_atomic_rejects_identical_paths(tmp_path: Path) -> None:
    staged_path = tmp_path / "payload.stage"
    staged_path.write_bytes(b"data")

    with pytest.raises(ValueError, match="different files"):
        FileSystem.publish_file_atomic(staged_path, staged_path)

    assert staged_path.is_file()


def test_directory_queries_require_an_existing_directory(tmp_path: Path) -> None:
    missing_directory = tmp_path / "missing"

    with pytest.raises(FileNotFoundError, match="directory does not exist"):
        FileSystem.scan_dir(missing_directory)
    with pytest.raises(FileNotFoundError, match="directory does not exist"):
        FileSystem.get_dir_size(missing_directory)


def test_remove_unlinks_directory_symlink_without_removing_target(
    tmp_path: Path,
) -> None:
    target_directory = tmp_path / "target"
    target_directory.mkdir()
    (target_directory / "kept.txt").write_text("kept", encoding="utf-8")
    link_path = tmp_path / "target-link"
    link_path.symlink_to(target_directory, target_is_directory=True)

    FileSystem.remove(link_path)

    assert not link_path.exists()
    assert (target_directory / "kept.txt").is_file()


def test_remove_unlinks_broken_symlink(tmp_path: Path) -> None:
    link_path = tmp_path / "broken-link"
    link_path.symlink_to(tmp_path / "missing-target")

    FileSystem.remove(link_path)

    assert not link_path.is_symlink()


def test_clean_temp_files_only_deletes_matching_files(tmp_path: Path) -> None:
    nested_directory = tmp_path / "nested.tmp"
    nested_directory.mkdir()
    temporary_file = nested_directory / "payload.tmp"
    kept_file = nested_directory / "payload.txt"
    temporary_file.write_text("remove", encoding="utf-8")
    kept_file.write_text("keep", encoding="utf-8")

    assert FileSystem.clean_temp_files(tmp_path) == 1
    assert nested_directory.is_dir()
    assert kept_file.is_file()

    with pytest.raises(ValueError, match="suffix"):
        FileSystem.clean_temp_files(tmp_path, suffix="")
