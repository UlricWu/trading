# filepath: tests/scripts/test_deploy_dispatcher.py
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "deploy_dispatcher.py"
COMMIT_SHA = "2d70f1742c126020d82b1bfa287859ffd99e1d6f"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("deploy_dispatcher", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _state_dir(tmp_path: Path) -> Path:
    for name in ("queue", "results"):
        (tmp_path / name).mkdir()
    return tmp_path


def _queue(state_dir: Path, delivery_id: str = "delivery-1") -> Path:
    path = state_dir / "queue" / f"{delivery_id}.json"
    path.write_text(
        json.dumps(
            {
                "delivery_id": delivery_id,
                "repository": "UlricWu/trading",
                "ref": "refs/heads/release/auto-release",
                "after": COMMIT_SHA,
                "received_at": "2026-08-20T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_dispatcher_passes_only_validated_identity_and_records_success(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = _load_script()
    state_dir = _state_dir(tmp_path)
    queue_path = _queue(state_dir)
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(arguments: list[str], *, check: bool, env: dict[str, str]) -> SimpleNamespace:
        calls.append((arguments, env))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    module._drain(state_dir, Path("/fixed/deploy"))

    assert calls[0][0] == ["/fixed/deploy"]
    assert calls[0][1]["RUN_ID"] == "delivery-1"
    assert calls[0][1]["DEPLOY_SHA"] == COMMIT_SHA
    assert not queue_path.exists()
    result = json.loads((state_dir / "results" / "delivery-1.json").read_text())
    assert result["status"] == "succeeded"
    assert result["exit_code"] == 0
    assert result["commit_sha"] == COMMIT_SHA


def test_dispatcher_persists_failure_and_continues(tmp_path: Path, monkeypatch: object) -> None:
    module = _load_script()
    state_dir = _state_dir(tmp_path)
    _queue(state_dir, "delivery-1")
    _queue(state_dir, "delivery-2")
    return_codes = iter((65, 0))

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda arguments, check, env: SimpleNamespace(returncode=next(return_codes)),
    )
    module._drain(state_dir, Path("/fixed/deploy"))

    first = json.loads((state_dir / "results" / "delivery-1.json").read_text())
    second = json.loads((state_dir / "results" / "delivery-2.json").read_text())
    assert first["status"] == "failed" and first["exit_code"] == 65
    assert second["status"] == "succeeded"
    assert list((state_dir / "queue").iterdir()) == []


def test_existing_result_removes_recreated_queue_marker_without_deploy(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = _load_script()
    state_dir = _state_dir(tmp_path)
    queue_path = _queue(state_dir)
    (state_dir / "results" / queue_path.name).write_text("{}\n", encoding="utf-8")
    called = False

    def run(arguments: list[str], *, check: bool, env: dict[str, str]) -> SimpleNamespace:
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    module._drain(state_dir, Path("/fixed/deploy"))

    assert not called
    assert not queue_path.exists()
