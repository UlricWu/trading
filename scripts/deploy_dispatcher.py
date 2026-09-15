#!/usr/bin/env python3
# filepath: scripts/deploy_dispatcher.py
"""Drain durable Webhook deliveries into the fixed deployment worker."""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


_DELIVERY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
_REPOSITORY = "UlricWu/trading"
_REF = "refs/heads/release/auto-release"


@dataclass(frozen=True)
class _DeploymentResult:
    delivery_id: str
    commit_sha: str
    status: str
    exit_code: int
    started_at: str
    finished_at: str


def _read_delivery(path: Path) -> tuple[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = {"delivery_id", "repository", "ref", "after", "received_at"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("invalid delivery record schema")
    delivery_id = payload["delivery_id"]
    commit_sha = payload["after"]
    if not isinstance(delivery_id, str) or _DELIVERY_PATTERN.fullmatch(delivery_id) is None:
        raise ValueError("invalid delivery id")
    if path.name != f"{delivery_id}.json":
        raise ValueError("delivery filename does not match its id")
    if payload["repository"] != _REPOSITORY or payload["ref"] != _REF:
        raise ValueError("invalid deployment source")
    if not isinstance(commit_sha, str) or _COMMIT_PATTERN.fullmatch(commit_sha) is None:
        raise ValueError("invalid commit sha")
    if not isinstance(payload["received_at"], str):
        raise ValueError("invalid received timestamp")
    return delivery_id, commit_sha


def _write_result(path: Path, result: _DeploymentResult) -> None:
    temp_path = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    encoded = json.dumps(
        asdict(result), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"
    try:
        with temp_path.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temp_path.unlink(missing_ok=True)


def _drain(state_dir: Path, deploy_script: Path) -> None:
    queue_dir = state_dir / "queue"
    results_dir = state_dir / "results"
    for queue_path in sorted(queue_dir.glob("*.json")):
        result_path = results_dir / queue_path.name
        if result_path.exists():
            queue_path.unlink(missing_ok=True)
            continue
        started_at = datetime.now(UTC).isoformat()
        try:
            delivery_id, commit_sha = _read_delivery(queue_path)
            completed = subprocess.run(
                [str(deploy_script)],
                check=False,
                env={**os.environ, "RUN_ID": delivery_id, "DEPLOY_SHA": commit_sha},
            )
            exit_code = completed.returncode
        except (OSError, ValueError, json.JSONDecodeError) as error:
            delivery_id = queue_path.stem
            commit_sha = ""
            exit_code = 127 if isinstance(error, OSError) else 65
        result = _DeploymentResult(
            delivery_id=delivery_id,
            commit_sha=commit_sha,
            status="succeeded" if exit_code == 0 else "failed",
            exit_code=exit_code,
            started_at=started_at,
            finished_at=datetime.now(UTC).isoformat(),
        )
        _write_result(result_path, result)
        queue_path.unlink()


def main() -> None:
    """Process every queued delivery once and persist its terminal result.

    Example:
        ``MINQUANT_DEPLOY_SCRIPT=/usr/local/libexec/minquant-deploy scripts/deploy_dispatcher.py``
    """
    state_dir = Path(os.environ.get("MINQUANT_WEBHOOK_STATE_DIR", "/var/lib/minquant-webhook"))
    deploy_script = Path(
        os.environ.get("MINQUANT_DEPLOY_SCRIPT", "/usr/local/libexec/minquant-deploy")
    )
    _drain(state_dir, deploy_script)


if __name__ == "__main__":
    main()
