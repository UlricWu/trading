# filepath: tests/scripts/test_github_webhook_receiver.py
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "github_webhook_receiver.py"
SECRET = b"test-webhook-secret"
COMMIT_SHA = "2d70f1742c126020d82b1bfa287859ffd99e1d6f"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("github_webhook_receiver", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _state_dir(tmp_path: Path) -> Path:
    for name in ("staging", "deliveries", "queue", "results"):
        (tmp_path / name).mkdir()
    return tmp_path


def _request(application: object, payload: dict[str, object], **headers: str) -> str:
    body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    environ: dict[str, object] = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/github/webhook",
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
        "HTTP_X_HUB_SIGNATURE_256": signature,
        "HTTP_X_GITHUB_EVENT": "push",
        "HTTP_X_GITHUB_DELIVERY": "delivery-1",
        **headers,
    }
    status = ""

    def start_response(value: str, response_headers: list[tuple[str, str]]) -> None:
        nonlocal status
        status = value

    list(application(environ, start_response))
    return status


def _payload() -> dict[str, object]:
    return {
        "repository": {"full_name": "UlricWu/trading"},
        "ref": "refs/heads/release/auto-release",
        "after": COMMIT_SHA,
    }


def test_valid_delivery_is_durable_and_duplicate_is_not_requeued(tmp_path: Path) -> None:
    module = _load_script()
    state_dir = _state_dir(tmp_path)
    application = module._WebhookApplication(SECRET, module._DeliveryStore(state_dir))

    assert _request(application, _payload()) == "202 Accepted"
    delivery_path = state_dir / "deliveries" / "delivery-1.json"
    queue_path = state_dir / "queue" / "delivery-1.json"
    assert delivery_path.exists()
    assert queue_path.exists()
    assert delivery_path.stat().st_ino == queue_path.stat().st_ino
    record = json.loads(delivery_path.read_text(encoding="utf-8"))
    assert record["after"] == COMMIT_SHA
    assert set(record) == {"delivery_id", "repository", "ref", "after", "received_at"}

    queue_path.unlink()
    (state_dir / "results" / "delivery-1.json").write_text("{}\n", encoding="utf-8")
    assert _request(application, _payload()) == "200 OK"
    assert not queue_path.exists()


def test_invalid_signature_is_rejected_without_persistence(tmp_path: Path) -> None:
    module = _load_script()
    state_dir = _state_dir(tmp_path)
    application = module._WebhookApplication(SECRET, module._DeliveryStore(state_dir))

    status = _request(application, _payload(), HTTP_X_HUB_SIGNATURE_256="sha256=wrong")

    assert status == "401 Unauthorized"
    assert list((state_dir / "deliveries").iterdir()) == []
    assert list((state_dir / "queue").iterdir()) == []


def test_wrong_repository_or_event_is_rejected(tmp_path: Path) -> None:
    module = _load_script()
    state_dir = _state_dir(tmp_path)
    application = module._WebhookApplication(SECRET, module._DeliveryStore(state_dir))
    payload = _payload()
    payload["repository"] = {"full_name": "someone/else"}

    assert _request(application, payload) == "422 Unprocessable Entity"
    assert _request(application, _payload(), HTTP_X_GITHUB_EVENT="ping") == (
        "422 Unprocessable Entity"
    )
    assert list((state_dir / "queue").iterdir()) == []
