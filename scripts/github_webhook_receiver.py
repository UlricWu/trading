#!/usr/bin/env python3
# filepath: scripts/github_webhook_receiver.py
"""Receive and durably enqueue the test deployment GitHub webhook."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from wsgiref.simple_server import make_server


_MAX_BODY_BYTES = 25 * 1024 * 1024
_DELIVERY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
_REPOSITORY = "UlricWu/trading"
_REF = "refs/heads/release/auto-release"
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _AcceptedDelivery:
    delivery_id: str
    repository: str
    ref: str
    after: str
    received_at: str


class _DeliveryStore:
    def __init__(self, state_dir: Path) -> None:
        self._staging = state_dir / "staging"
        self._deliveries = state_dir / "deliveries"
        self._queue = state_dir / "queue"
        self._results = state_dir / "results"

    def accept(self, delivery: _AcceptedDelivery) -> bool:
        name = f"{delivery.delivery_id}.json"
        delivery_path = self._deliveries / name
        queue_path = self._queue / name
        result_path = self._results / name
        temp_path = self._staging / f".{delivery.delivery_id}.{secrets.token_hex(8)}.tmp"
        payload = json.dumps(
            asdict(delivery), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"

        try:
            with temp_path.open("xb") as stream:
                os.fchmod(stream.fileno(), 0o640)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temp_path, delivery_path)
                accepted = True
                self._fsync_directory(self._deliveries)
            except FileExistsError:
                accepted = False

            if not result_path.exists():
                try:
                    os.link(delivery_path, queue_path)
                    self._fsync_directory(self._queue)
                except FileExistsError:
                    pass
            return accepted
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class _WebhookApplication:
    def __init__(self, secret: bytes, store: _DeliveryStore) -> None:
        self._secret = secret
        self._store = store

    def __call__(self, environ: dict[str, object], start_response: object) -> Iterable[bytes]:
        try:
            status, message = self._handle(environ)
        except Exception:
            _LOGGER.exception("webhook request failed")
            status, message = "500 Internal Server Error", "internal error"
        body = f"{message}\n".encode("utf-8")
        start_response(
            status,
            [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body)))],
        )
        return [body]

    def _handle(self, environ: dict[str, object]) -> tuple[str, str]:
        if environ.get("REQUEST_METHOD") != "POST" or environ.get("PATH_INFO") != "/github/webhook":
            return "404 Not Found", "not found"
        length_text = str(environ.get("CONTENT_LENGTH", ""))
        if not length_text.isdigit():
            return "400 Bad Request", "invalid content length"
        length = int(length_text)
        if length > _MAX_BODY_BYTES:
            return "413 Payload Too Large", "payload too large"
        body = environ["wsgi.input"].read(length)
        signature = str(environ.get("HTTP_X_HUB_SIGNATURE_256", ""))
        expected = "sha256=" + hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return "401 Unauthorized", "invalid signature"
        if environ.get("HTTP_X_GITHUB_EVENT") != "push":
            return "422 Unprocessable Entity", "unsupported event"

        delivery_id = str(environ.get("HTTP_X_GITHUB_DELIVERY", ""))
        if _DELIVERY_PATTERN.fullmatch(delivery_id) is None:
            return "422 Unprocessable Entity", "invalid delivery id"
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "400 Bad Request", "invalid json"
        if not isinstance(payload, dict):
            return "422 Unprocessable Entity", "invalid payload"
        repository = payload.get("repository")
        full_name = repository.get("full_name") if isinstance(repository, dict) else None
        ref = payload.get("ref")
        after = payload.get("after")
        if full_name != _REPOSITORY or ref != _REF or not isinstance(after, str):
            return "422 Unprocessable Entity", "unexpected deployment source"
        if _COMMIT_PATTERN.fullmatch(after) is None:
            return "422 Unprocessable Entity", "invalid commit sha"

        accepted = self._store.accept(
            _AcceptedDelivery(
                delivery_id=delivery_id,
                repository=_REPOSITORY,
                ref=_REF,
                after=after,
                received_at=datetime.now(UTC).isoformat(),
            )
        )
        if accepted:
            return "202 Accepted", "accepted"
        return "200 OK", "duplicate"


def _secret_path() -> Path:
    configured = os.environ.get("MINQUANT_WEBHOOK_SECRET_FILE")
    if configured:
        return Path(configured)
    credentials_directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not credentials_directory:
        raise RuntimeError("CREDENTIALS_DIRECTORY is required")
    return Path(credentials_directory) / "github-webhook-secret"


def main() -> None:
    """Run the loopback-only Webhook receiver.

    Example:
        ``MINQUANT_WEBHOOK_SECRET_FILE=/run/secret python scripts/github_webhook_receiver.py``
    """
    host = os.environ.get("MINQUANT_WEBHOOK_HOST", "127.0.0.1")
    port = int(os.environ.get("MINQUANT_WEBHOOK_PORT", "9000"))
    state_dir = Path(os.environ.get("MINQUANT_WEBHOOK_STATE_DIR", "/var/lib/minquant-webhook"))
    secret = _secret_path().read_bytes().rstrip(b"\r\n")
    if not secret:
        raise RuntimeError("Webhook secret must not be empty")
    application = _WebhookApplication(secret, _DeliveryStore(state_dir))
    with make_server(host, port, application) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
