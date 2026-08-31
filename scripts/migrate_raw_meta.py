# filepath: scripts/migrate_raw_meta.py
"""Migrate identity-verified legacy raw Meta to the current schema.

The script scans only existing ``raw/**/meta.json`` objects. It never creates
Meta for an orphan payload. The default invocation performs a complete dry-run;
``--apply`` publishes current Meta only when the preflight has no blocked object.

Example:
    Run from the repository root to inspect the whole formal storage first::

        python -m scripts.migrate_raw_meta --storage-root STORAGE_ROOT

    Stop every raw producer, then apply the exact same scan::

        python -m scripts.migrate_raw_meta --storage-root STORAGE_ROOT --apply

Both successful modes exit ``0``. Invalid arguments exit ``2``. A blocked
object or an execution failure exits ``1``; apply never starts when preflight
contains a blocked object.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from src.access import meta
from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class _Arguments:
    storage_root: Path
    should_apply: bool


@dataclass(frozen=True, slots=True)
class _MigrationCandidate:
    meta_path: Path
    payload_path: Path
    size_bytes: int


@dataclass(frozen=True, slots=True)
class _BlockedMeta:
    meta_path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class _MigrationPlan:
    current_count: int
    candidates: tuple[_MigrationCandidate, ...]
    blocked: tuple[_BlockedMeta, ...]


class _LegacyMetaError(RuntimeError):
    pass


def _raise_scan_error(error: OSError) -> None:
    raise error


def _parse_arguments(argv: Sequence[str]) -> _Arguments:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate identity-verified legacy raw Meta records to the schema "
            "published by the current src.access.meta implementation."
        ),
        epilog=(
            "Examples:\n"
            "  Dry-run preflight:\n"
            "    python -m scripts.migrate_raw_meta "
            "--storage-root /absolute/storage/root\n"
            "  Apply after a clean preflight and stopping raw producers:\n"
            "    python -m scripts.migrate_raw_meta "
            "--storage-root /absolute/storage/root --apply"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--storage-root",
        required=True,
        help="Existing absolute formal storage root.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Publish migrated Meta after a clean preflight; default is dry-run.",
    )
    namespace = parser.parse_args(argv)
    storage_root = namespace.storage_root
    should_apply = namespace.apply
    if not isinstance(storage_root, str) or not isinstance(should_apply, bool):
        raise TypeError("argparse returned invalid migration arguments")
    return _Arguments(
        storage_root=Path(storage_root),
        should_apply=should_apply,
    )


def _require_formal_raw_meta_path(
    *,
    pm: PathManager,
    meta_path: Path,
) -> Path:
    if meta_path.is_symlink() or not meta_path.is_file():
        raise _LegacyMetaError("Meta must be an existing non-symlink file")

    resolved_meta = meta_path.resolve(strict=True)
    if resolved_meta != meta_path:
        raise _LegacyMetaError("Meta path must not traverse symbolic links")

    raw_root = pm.storage_root / "raw"
    try:
        relative = resolved_meta.relative_to(raw_root)
    except ValueError as exc:
        raise _LegacyMetaError("Meta must be below the formal raw root") from exc
    if len(relative.parts) != 4 or relative.name != "meta.json":
        raise _LegacyMetaError("Meta path is not a formal raw partition")

    broker, source_name, partition, _ = relative.parts
    try:
        PathManager.require_safe_basename(broker, "broker")
        PathManager.require_safe_basename(source_name, "source_name")
        if partition.startswith("trade_date="):
            trade_date = DateTimeUtils.require_system_date(
                partition.removeprefix("trade_date="),
                field_name="raw partition trade_date",
            )
            expected_meta = pm.raw_meta(
                broker=broker,
                source_name=source_name,
                trade_date=trade_date,
            )
        elif (
            broker == "tushare"
            and source_name == "trade_calendar"
            and partition.startswith("year=")
        ):
            year_text = partition.removeprefix("year=")
            if len(year_text) != 4 or not year_text.isdigit():
                raise ValueError("raw calendar year must use YYYY")
            expected_meta = pm.raw_year_meta(
                broker=broker,
                source_name=source_name,
                calendar_year=int(year_text),
            )
        else:
            raise ValueError("raw partition must use trade_date=YYYY-MM-DD")
    except (TypeError, ValueError) as exc:
        raise _LegacyMetaError(str(exc)) from exc

    if expected_meta != resolved_meta:
        raise _LegacyMetaError("Meta path does not match its formal raw identity")
    return resolved_meta


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    legacy_record: dict[str, object] = {}
    for key, value in pairs:
        if key in legacy_record:
            raise ValueError(f"duplicate JSON key: {key}")
        legacy_record[key] = value
    return legacy_record


def _load_legacy_candidate(
    *,
    pm: PathManager,
    meta_path: Path,
) -> _MigrationCandidate:
    try:
        with meta_path.open("r", encoding="utf-8") as handle:
            legacy_record = json.load(
                handle,
                object_pairs_hook=_reject_duplicate_keys,
            )
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise _LegacyMetaError(f"legacy Meta JSON is invalid: {exc}") from exc
    if not isinstance(legacy_record, dict):
        raise _LegacyMetaError("legacy Meta root must be an object")
    if "upstream" in legacy_record or "symbol_slices" in legacy_record:
        raise _LegacyMetaError(
            "raw legacy Meta must not contain upstream or symbol_slices"
        )

    if legacy_record.get("version") != "V1.0":
        raise _LegacyMetaError("legacy version must be V1.0")
    if legacy_record.get("upstreams") != []:
        raise _LegacyMetaError("raw legacy upstreams must be an empty array")

    legacy_output = legacy_record.get("output")
    if not isinstance(legacy_output, dict):
        raise _LegacyMetaError("legacy output must be an object")
    legacy_payload_text = legacy_output.get("path")
    if not isinstance(legacy_payload_text, str):
        raise _LegacyMetaError("legacy output.path must be a string")
    legacy_payload_path = PurePosixPath(legacy_payload_text)
    if (
        not legacy_payload_path.is_absolute()
        or legacy_payload_path.as_posix() != legacy_payload_text
    ):
        raise _LegacyMetaError(
            "legacy output.path must be a normalized absolute POSIX path"
        )

    payload_name = legacy_payload_path.name
    try:
        PathManager.require_safe_basename(payload_name, "legacy payload")
    except (TypeError, ValueError) as exc:
        raise _LegacyMetaError(str(exc)) from exc

    legacy_fingerprint = legacy_output.get("fingerprint")
    if not isinstance(legacy_fingerprint, dict):
        raise _LegacyMetaError("legacy output.fingerprint must be an object")
    recorded_size = legacy_fingerprint.get("size")
    if (
        not isinstance(recorded_size, int)
        or isinstance(recorded_size, bool)
        or recorded_size < 0
    ):
        raise _LegacyMetaError(
            "legacy output.fingerprint.size must be a non-negative integer"
        )

    payload_path = meta_path.parent / payload_name
    if payload_path.is_symlink() or not payload_path.is_file():
        raise _LegacyMetaError(
            "legacy payload must be an existing non-symlink sibling file"
        )
    resolved_payload = payload_path.resolve(strict=True)
    if (
        resolved_payload.parent != meta_path.parent
        or not resolved_payload.is_relative_to(pm.storage_root)
    ):
        raise _LegacyMetaError(
            "legacy payload must resolve to a sibling below storage_root"
        )

    current_relative_payload = PurePosixPath(
        resolved_payload.relative_to(pm.storage_root).as_posix()
    )
    suffix_size = len(current_relative_payload.parts)
    if legacy_payload_path.parts[-suffix_size:] != current_relative_payload.parts:
        raise _LegacyMetaError(
            "legacy output.path does not match the current raw identity"
        )

    actual_size = FileSystem.get_file_size(resolved_payload)
    if actual_size != recorded_size:
        raise _LegacyMetaError(
            f"legacy payload size changed: recorded={recorded_size}, "
            f"actual={actual_size}"
        )

    try:
        unexpected_siblings = sorted(
            sibling.name
            for sibling in meta_path.parent.iterdir()
            if sibling.name not in {"meta.json", payload_name}
        )
    except OSError as exc:
        raise _LegacyMetaError(
            f"failed to inspect raw partition siblings: {exc}"
        ) from exc
    if unexpected_siblings:
        raise _LegacyMetaError(
            f"raw partition has unexpected siblings: {unexpected_siblings}"
        )

    return _MigrationCandidate(
        meta_path=meta_path,
        payload_path=resolved_payload,
        size_bytes=recorded_size,
    )


def _build_migration_plan(pm: PathManager) -> _MigrationPlan:
    current_count = 0
    candidates: list[_MigrationCandidate] = []
    blocked: list[_BlockedMeta] = []
    raw_root = pm.storage_root / "raw"

    discovered_meta_paths: list[Path] = []
    for directory_text, directory_names, file_names in os.walk(
        raw_root,
        topdown=True,
        onerror=_raise_scan_error,
        followlinks=False,
    ):
        directory_path = Path(directory_text)
        for directory_name in tuple(directory_names):
            child_directory = directory_path / directory_name
            if child_directory.is_symlink() or directory_name == "meta.json":
                directory_names.remove(directory_name)
                blocked.append(
                    _BlockedMeta(
                        meta_path=child_directory,
                        reason="raw scan encountered a symlink or directory Meta",
                    )
                )
        if "meta.json" in file_names:
            discovered_meta_paths.append(directory_path / "meta.json")

    for discovered_meta in sorted(discovered_meta_paths):
        try:
            meta_path = _require_formal_raw_meta_path(
                pm=pm,
                meta_path=discovered_meta,
            )
            try:
                meta.require(pm=pm, meta_path=meta_path)
            except (FileNotFoundError, RuntimeError) as current_error:
                try:
                    candidate = _load_legacy_candidate(
                        pm=pm,
                        meta_path=meta_path,
                    )
                except _LegacyMetaError as legacy_error:
                    raise _LegacyMetaError(
                        f"current validation failed ({current_error}); "
                        f"legacy identity is not migratable ({legacy_error})"
                    ) from legacy_error
                candidates.append(candidate)
            else:
                current_count += 1
        except (OSError, TypeError, ValueError, _LegacyMetaError) as exc:
            blocked.append(
                _BlockedMeta(
                    meta_path=discovered_meta,
                    reason=str(exc),
                )
            )

    return _MigrationPlan(
        current_count=current_count,
        candidates=tuple(candidates),
        blocked=tuple(blocked),
    )


def _relative_path(*, pm: PathManager, path: Path) -> str:
    try:
        return path.relative_to(pm.storage_root).as_posix()
    except ValueError:
        return path.as_posix()


def _print_plan(*, pm: PathManager, plan: _MigrationPlan) -> None:
    for candidate in plan.candidates:
        print(
            f"MIGRATABLE meta_path={_relative_path(pm=pm, path=candidate.meta_path)} "
            f"payload_path={_relative_path(pm=pm, path=candidate.payload_path)} "
            f"size_bytes={candidate.size_bytes}"
        )
    for blocked_meta in plan.blocked:
        reason = json.dumps(blocked_meta.reason, ensure_ascii=False)
        print(
            f"BLOCKED meta_path="
            f"{_relative_path(pm=pm, path=blocked_meta.meta_path)} "
            f"reason={reason}"
        )


def _apply_migration_plan(*, pm: PathManager, plan: _MigrationPlan) -> int:
    migrated_count = 0
    for candidate in plan.candidates:
        actual_size = FileSystem.get_file_size(candidate.payload_path)
        if actual_size != candidate.size_bytes:
            raise RuntimeError(
                f"payload changed after preflight: "
                f"path={candidate.payload_path}, "
                f"preflight={candidate.size_bytes}, actual={actual_size}"
            )
        meta.commit(pm=pm, payload_path=candidate.payload_path)
        loaded = meta.require(
            pm=pm,
            meta_path=candidate.meta_path,
            expected_payload_path=candidate.payload_path,
        )
        if loaded.size_bytes != candidate.size_bytes:
            raise RuntimeError(
                f"migrated payload identity changed: path={candidate.payload_path}"
            )
        migrated_count += 1
        print(
            f"MIGRATED meta_path="
            f"{_relative_path(pm=pm, path=candidate.meta_path)} "
            f"payload_path={_relative_path(pm=pm, path=candidate.payload_path)} "
            f"size_bytes={candidate.size_bytes}"
        )
    return migrated_count


def _run(argv: Sequence[str]) -> int:
    arguments = _parse_arguments(argv)
    pm = PathManager(arguments.storage_root)
    plan = _build_migration_plan(pm)
    _print_plan(pm=pm, plan=plan)

    migrated_count = 0
    mode = "dry-run"
    if arguments.should_apply:
        mode = "apply"
        if not plan.blocked:
            migrated_count = _apply_migration_plan(pm=pm, plan=plan)

    print(
        f"SUMMARY mode={mode} current={plan.current_count} "
        f"migratable={len(plan.candidates)} migrated={migrated_count} "
        f"blocked={len(plan.blocked)}"
    )
    return 1 if plan.blocked else 0


def _main(argv: Sequence[str]) -> int:
    try:
        return _run(argv)
    except Exception as exc:
        print(f"migration failed; reason={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
