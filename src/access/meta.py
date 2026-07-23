# filepath: src/access/meta.py
"""Read and write the object-side metadata owned by formal storage."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from src.utils.filesystem import FileSystem
from src.utils.path import PathManager

__all__ = ("MetaRecord", "load", "write")

_REQUIRED_FIELDS = frozenset({"payload", "size_bytes"})
_OPTIONAL_FIELDS = frozenset({"upstream", "symbol_slices"})


@dataclass(frozen=True, slots=True)
class MetaRecord:
    """One object-side Meta record.

    `upstream` is `(storage-relative Meta path, recorded payload size)`.
    """

    payload_path: Path
    size_bytes: int
    upstream: tuple[PurePosixPath, int] | None
    symbol_slices: Mapping[str, range] | None

    def __post_init__(self) -> None:
        if self.symbol_slices is not None:
            object.__setattr__(
                self,
                "symbol_slices",
                MappingProxyType(dict(self.symbol_slices)),
            )


def load(
    *,
    meta_path: Path,
    storage_root: Path,
    expected_payload_path: Path | None = None,
) -> MetaRecord | None:
    """Return verified metadata, or `None` when `meta.json` is absent."""
    root = _resolve_storage_root(storage_root)
    expected_path = None
    if expected_payload_path is not None:
        expected_path = _resolve_storage_path(
            expected_payload_path,
            storage_root=root,
            field_name="expected_payload_path",
            require_file=False,
        )

    record = _load_record(meta_path=meta_path, storage_root=root)
    if record is None:
        return None
    if expected_path is not None and record.payload_path != expected_path:
        raise RuntimeError(
            f"Meta payload does not match expected payload: "
            f"meta_path={meta_path}, payload={record.payload_path}, "
            f"expected={expected_path}"
        )
    return record


def write(
    *,
    payload_path: Path,
    storage_root: Path,
    upstream_meta_path: Path | None = None,
    symbol_slices: Mapping[str, range] | None = None,
) -> None:
    """Write Meta for an existing payload and optional direct relationships."""
    root = _resolve_storage_root(storage_root)
    resolved_payload = _resolve_storage_path(
        payload_path,
        storage_root=root,
        field_name="payload_path",
        require_file=True,
    )
    PathManager.require_safe_basename(resolved_payload.name, "payload_path.name")
    data: dict[str, object] = {
        "payload": resolved_payload.name,
        "size_bytes": FileSystem.get_file_size(resolved_payload),
    }

    if upstream_meta_path is not None:
        upstream_path = _resolve_meta_path(
            upstream_meta_path,
            storage_root=root,
            require_file=True,
        )
        upstream_record = _read_record(
            meta_path=upstream_path,
            storage_root=root,
        )
        if upstream_record is None:
            raise FileNotFoundError(upstream_path)
        data["upstream"] = {
            "meta_path": upstream_path.relative_to(root).as_posix(),
            "size_bytes": upstream_record.size_bytes,
        }

    if symbol_slices is not None:
        validated_slices = _validate_symbol_slices(symbol_slices)
        data["symbol_slices"] = {
            symbol: {
                "start": rows.start,
                "end": rows.stop,
            }
            for symbol, rows in validated_slices.items()
        }

    encoded = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
    FileSystem.write_bytes_atomic(resolved_payload.parent / "meta.json", encoded)


def _load_record(
    *,
    meta_path: Path,
    storage_root: Path,
) -> MetaRecord | None:
    resolved_meta = _resolve_meta_path(
        meta_path,
        storage_root=storage_root,
        require_file=False,
    )
    record = _read_record(meta_path=resolved_meta, storage_root=storage_root)
    if record is None:
        return None

    if record.upstream is None:
        return record

    upstream_meta_path, upstream_size_bytes = record.upstream
    upstream_path = _resolve_meta_path(
        storage_root.joinpath(*upstream_meta_path.parts),
        storage_root=storage_root,
        require_file=True,
    )
    upstream_record = _read_record(
        meta_path=upstream_path,
        storage_root=storage_root,
    )
    if upstream_record is None:
        raise FileNotFoundError(upstream_path)
    if upstream_record.size_bytes != upstream_size_bytes:
        raise RuntimeError(
            f"upstream payload size changed: meta_path={upstream_path}, "
            f"recorded={upstream_size_bytes}, "
            f"actual={upstream_record.size_bytes}"
        )
    return record


def _read_record(
    *,
    meta_path: Path,
    storage_root: Path,
) -> MetaRecord | None:
    data = _read_json_object(meta_path)
    if data is None:
        return None

    fields = set(data)
    missing = _REQUIRED_FIELDS - fields
    unknown = fields - _REQUIRED_FIELDS - _OPTIONAL_FIELDS
    if missing or unknown:
        raise RuntimeError(
            f"invalid Meta fields: meta_path={meta_path}, "
            f"missing={sorted(missing)}, unknown={sorted(unknown)}"
        )

    payload_name = data["payload"]
    if not isinstance(payload_name, str):
        raise RuntimeError(f"Meta payload must be a string: {meta_path}")
    try:
        PathManager.require_safe_basename(payload_name, "payload")
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid Meta payload: {meta_path}") from exc

    recorded_size = _require_non_negative_int(
        data["size_bytes"],
        field_name="size_bytes",
        context=str(meta_path),
    )
    payload_path = _resolve_storage_path(
        meta_path.parent / payload_name,
        storage_root=storage_root,
        field_name="Meta payload",
        require_file=True,
    )
    if payload_path.parent != meta_path.parent:
        raise RuntimeError(
            f"Meta payload must be a sibling file: "
            f"meta_path={meta_path}, payload={payload_path}"
        )
    actual_size = FileSystem.get_file_size(payload_path)
    if actual_size != recorded_size:
        raise RuntimeError(
            f"payload size changed: path={payload_path}, "
            f"recorded={recorded_size}, actual={actual_size}"
        )

    upstream = None
    if "upstream" in data:
        raw_upstream = data["upstream"]
        if not isinstance(raw_upstream, Mapping) or set(raw_upstream) != {
            "meta_path",
            "size_bytes",
        }:
            raise RuntimeError(f"invalid Meta upstream: {meta_path}")
        relative_path = raw_upstream["meta_path"]
        if not isinstance(relative_path, str):
            raise RuntimeError(f"upstream meta_path must be a string: {meta_path}")
        upstream = (
            _require_upstream_relative_path(relative_path),
            _require_non_negative_int(
                raw_upstream["size_bytes"],
                field_name="upstream.size_bytes",
                context=str(meta_path),
            ),
        )

    symbol_slices = None
    if "symbol_slices" in data:
        raw_slices = data["symbol_slices"]
        if not isinstance(raw_slices, Mapping):
            raise RuntimeError(f"Meta symbol_slices must be an object: {meta_path}")
        parsed_slices: dict[str, range] = {}
        for symbol, bounds in raw_slices.items():
            if not isinstance(bounds, Mapping) or set(bounds) != {"start", "end"}:
                raise RuntimeError(
                    f"invalid symbol slice bounds: "
                    f"meta_path={meta_path}, symbol={symbol!r}"
                )
            start = _require_non_negative_int(
                bounds["start"],
                field_name="start",
                context=f"symbol={symbol!r}",
            )
            end = _require_non_negative_int(
                bounds["end"],
                field_name="end",
                context=f"symbol={symbol!r}",
            )
            parsed_slices[symbol] = range(start, end)
        symbol_slices = _validate_symbol_slices(parsed_slices)

    return MetaRecord(
        payload_path=payload_path,
        size_bytes=recorded_size,
        upstream=upstream,
        symbol_slices=symbol_slices,
    )


def _read_json_object(meta_path: Path) -> dict[str, object] | None:
    try:
        with meta_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle, object_pairs_hook=_reject_duplicate_keys)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError(f"invalid Meta JSON: {meta_path}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Meta root must be an object: {meta_path}")
    return data


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    data: dict[str, object] = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f"duplicate JSON key: {key}")
        data[key] = value
    return data


def _validate_symbol_slices(
    symbol_slices: Mapping[str, range],
) -> dict[str, range]:
    if not symbol_slices:
        raise RuntimeError("symbol_slices must be non-empty")

    validated: dict[str, range] = {}
    for symbol, rows in symbol_slices.items():
        if not isinstance(symbol, str) or not symbol:
            raise RuntimeError("symbol_slices keys must be non-empty strings")
        if not isinstance(rows, range) or rows.step != 1:
            raise RuntimeError(
                f"symbol slice must be a unit-step range: symbol={symbol!r}"
            )
        if rows.start < 0 or rows.start >= rows.stop:
            raise RuntimeError(
                f"symbol slice must satisfy 0 <= start < end: "
                f"symbol={symbol!r}, start={rows.start}, end={rows.stop}"
            )
        validated[symbol] = rows

    ordered = sorted(validated.items(), key=lambda item: item[1].start)
    expected_start = 0
    for symbol, rows in ordered:
        if rows.start != expected_start:
            raise RuntimeError(
                f"symbol slices must be contiguous from zero: "
                f"symbol={symbol!r}, expected_start={expected_start}, "
                f"actual_start={rows.start}"
            )
        expected_start = rows.stop
    return dict(ordered)


def _require_non_negative_int(
    value: object,
    *,
    field_name: str,
    context: str,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError(f"{field_name} must be a non-negative integer: {context}")
    return value


def _resolve_storage_root(storage_root: Path) -> Path:
    if not isinstance(storage_root, Path):
        raise TypeError("storage_root must be a pathlib.Path")
    if not storage_root.is_absolute():
        raise ValueError("storage_root must be an absolute path")
    resolved = storage_root.resolve(strict=True)
    if not resolved.is_dir():
        raise NotADirectoryError(resolved)
    return resolved


def _resolve_meta_path(
    meta_path: Path,
    *,
    storage_root: Path,
    require_file: bool,
) -> Path:
    resolved = _resolve_storage_path(
        meta_path,
        storage_root=storage_root,
        field_name="meta_path",
        require_file=require_file,
    )
    if resolved.name != "meta.json":
        raise ValueError(f"meta_path must end with meta.json: {resolved}")
    return resolved


def _resolve_storage_path(
    path: Path,
    *,
    storage_root: Path,
    field_name: str,
    require_file: bool,
) -> Path:
    if not isinstance(path, Path):
        raise TypeError(f"{field_name} must be a pathlib.Path")
    if not path.is_absolute():
        raise ValueError(f"{field_name} must be an absolute path")
    resolved = path.resolve(strict=require_file)
    if not resolved.is_relative_to(storage_root):
        raise ValueError(f"{field_name} must be below storage_root: {resolved}")
    if require_file and not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def _require_upstream_relative_path(value: str) -> PurePosixPath:
    if not value or "\\" in value or "\x00" in value:
        raise RuntimeError("upstream meta_path must be a non-empty POSIX path")
    relative_path = PurePosixPath(value)
    if (
        relative_path.is_absolute()
        or relative_path.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative_path.parts)
        or relative_path.name != "meta.json"
    ):
        raise RuntimeError(
            f"upstream meta_path must be storage-root relative: {value!r}"
        )
    return relative_path
