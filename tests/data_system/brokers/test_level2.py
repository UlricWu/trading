# filepath: tests/data_system/brokers/test_level2.py

"""Regression tests for the Level-2 broker download boundary."""

from __future__ import annotations

import ftplib
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from src.config.app_config import AppConfig
from src.config.data_config import DownloadBackend
from src.data_system.brokers import level2 as level2_module
from src.data_system.brokers.base import DownloadPlan
from src.data_system.brokers.level2 import FtpEndpoint, Level2Broker
from src.utils.path import PathManager


class PayloadFtp(ftplib.FTP):
    """Expose one deterministic Level-2 archive through the FTP API."""

    def __init__(
        self,
        *,
        remote_file: str,
        payload: bytes,
        remote_names: Sequence[str],
        declared_size_bytes: int | None = None,
        retrieval_error: BaseException | None = None,
    ) -> None:
        self.remote_file = remote_file
        self.payload = payload
        self.remote_names = tuple(remote_names)
        self.declared_size_bytes = declared_size_bytes
        self.retrieval_error = retrieval_error
        self.cwd_calls: list[str] = []
        self.retrievals: list[tuple[str, int | None]] = []
        self.is_closed = False

    def cwd(self, dirname: str) -> str:
        self.cwd_calls.append(dirname)
        return "250 directory changed"

    def nlst(self, *args: str) -> list[str]:
        return list(self.remote_names)

    def size(self, filename: str) -> int:
        assert filename == self.remote_file
        return (
            len(self.payload)
            if self.declared_size_bytes is None
            else self.declared_size_bytes
        )

    def retrbinary(
        self,
        cmd: str,
        callback: Callable[[bytes], object],
        blocksize: int = 8192,
        rest: int | None = None,
    ) -> str:
        self.retrievals.append((cmd, rest))
        callback(self.payload[rest or 0 :])
        if self.retrieval_error is not None:
            raise self.retrieval_error
        return "226 transfer complete"

    def close(self) -> None:
        self.is_closed = True


class RecordingBrokerLogger:
    """Record non-sensitive Level-2 operational messages."""

    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.warning_messages: list[str] = []

    def info(self, message: str) -> None:
        self.info_messages.append(message)

    def warning(self, message: str) -> None:
        self.warning_messages.append(message)


def _build_level2_broker() -> Level2Broker:
    app_config = SimpleNamespace(
        secret=SimpleNamespace(
            ftp_host="ftp.example.test",
            ftp_port=21,
            ftp_user="user",
            ftp_password="password",
        ),
        data=SimpleNamespace(
            brokers={
                "level2_ftp": SimpleNamespace(
                    remote_root="level2",
                    ftp_backend=DownloadBackend.FTPLIB,
                )
            }
        ),
    )
    return Level2Broker(app_cfg=cast("AppConfig", app_config))


def test_ftp_endpoint_normalizes_remote_root_without_exposing_password() -> None:
    endpoint = FtpEndpoint(
        host="ftp.example.test",
        port=21,
        user="user",
        password="secret",
        remote_root="/level2/",
    )

    assert endpoint.remote_root == "level2"
    assert "secret" not in repr(endpoint)


def test_level2_broker_downloads_and_publishes_source_native_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_file = "SZ_Trade.csv.7z"
    payload = b"source-native-level2"
    ftp = PayloadFtp(
        remote_file=remote_file,
        payload=payload,
        remote_names=(remote_file,),
    )
    monkeypatch.setattr(
        level2_module, "_probe_control_connection", lambda endpoint: None
    )
    monkeypatch.setattr(
        level2_module,
        "_connect_download_session",
        lambda endpoint: ftp,
    )
    path_manager = PathManager(tmp_path)
    broker = _build_level2_broker()

    downloaded_plan = broker.fetch(
        record=DownloadPlan(
            source_name="sz_trade",
            trade_date="2026-07-20",
            broker="level2_ftp",
            raw_object="SZ_Trade",
        ),
        pm=path_manager,
    )

    assert downloaded_plan is not None
    assert downloaded_plan.payload_file == remote_file
    assert ftp.cwd_calls == ["level2", "2026-07-20"]
    assert ftp.retrievals == [(f"RETR {remote_file}", None)]
    assert ftp.is_closed is True
    assert (
        path_manager.raw_payload(
            broker="level2_ftp",
            source_name="sz_trade",
            trade_date="2026-07-20",
            payload_file=remote_file,
        ).read_bytes()
        == payload
    )


def test_level2_broker_resumes_a_partial_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_file = "SZ_Trade.csv.7z"
    payload = b"source-native-level2"
    resume_offset_bytes = 7
    ftp = PayloadFtp(
        remote_file=remote_file,
        payload=payload,
        remote_names=(remote_file,),
    )
    monkeypatch.setattr(
        level2_module, "_probe_control_connection", lambda endpoint: None
    )
    monkeypatch.setattr(
        level2_module,
        "_connect_download_session",
        lambda endpoint: ftp,
    )
    path_manager = PathManager(tmp_path)
    staging_file = path_manager.staging_payload(
        broker="level2_ftp",
        source_name="sz_trade",
        trade_date="2026-07-20",
        payload_file=remote_file,
    )
    staging_file.parent.mkdir(parents=True, exist_ok=True)
    part_file = staging_file.with_name(f"{staging_file.name}.part")
    part_file.write_bytes(payload[:resume_offset_bytes])

    downloaded_plan = _build_level2_broker().fetch(
        record=DownloadPlan(
            source_name="sz_trade",
            trade_date="2026-07-20",
            broker="level2_ftp",
            raw_object="SZ_Trade",
        ),
        pm=path_manager,
    )

    assert downloaded_plan is not None
    assert ftp.retrievals == [(f"RETR {remote_file}", resume_offset_bytes)]
    assert staging_file.read_bytes() == payload
    assert not part_file.exists()


def test_level2_broker_reuses_a_complete_staging_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_file = "SZ_Trade.csv.7z"
    payload = b"source-native-level2"
    ftp = PayloadFtp(
        remote_file=remote_file,
        payload=payload,
        remote_names=(remote_file,),
    )
    monkeypatch.setattr(
        level2_module, "_probe_control_connection", lambda endpoint: None
    )
    monkeypatch.setattr(
        level2_module,
        "_connect_download_session",
        lambda endpoint: ftp,
    )
    path_manager = PathManager(tmp_path)
    staging_file = path_manager.staging_payload(
        broker="level2_ftp",
        source_name="sz_trade",
        trade_date="2026-07-20",
        payload_file=remote_file,
    )
    staging_file.parent.mkdir(parents=True, exist_ok=True)
    staging_file.write_bytes(payload)

    downloaded_plan = _build_level2_broker().fetch(
        record=DownloadPlan(
            source_name="sz_trade",
            trade_date="2026-07-20",
            broker="level2_ftp",
            raw_object="SZ_Trade",
        ),
        pm=path_manager,
    )

    assert downloaded_plan is not None
    assert ftp.retrievals == []
    assert staging_file.read_bytes() == payload


def test_level2_broker_restarts_an_oversized_partial_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_file = "SZ_Trade.csv.7z"
    payload = b"source-native-level2"
    ftp = PayloadFtp(
        remote_file=remote_file,
        payload=payload,
        remote_names=(remote_file,),
    )
    monkeypatch.setattr(
        level2_module, "_probe_control_connection", lambda endpoint: None
    )
    monkeypatch.setattr(
        level2_module,
        "_connect_download_session",
        lambda endpoint: ftp,
    )
    path_manager = PathManager(tmp_path)
    staging_file = path_manager.staging_payload(
        broker="level2_ftp",
        source_name="sz_trade",
        trade_date="2026-07-20",
        payload_file=remote_file,
    )
    staging_file.parent.mkdir(parents=True, exist_ok=True)
    part_file = staging_file.with_name(f"{staging_file.name}.part")
    part_file.write_bytes(payload + b"oversized")

    downloaded_plan = _build_level2_broker().fetch(
        record=DownloadPlan(
            source_name="sz_trade",
            trade_date="2026-07-20",
            broker="level2_ftp",
            raw_object="SZ_Trade",
        ),
        pm=path_manager,
    )

    assert downloaded_plan is not None
    assert ftp.retrievals == [(f"RETR {remote_file}", None)]
    assert staging_file.read_bytes() == payload


def test_level2_broker_rejects_a_download_size_mismatch_and_closes_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_file = "SZ_Trade.csv.7z"
    payload = b"short-payload"
    ftp = PayloadFtp(
        remote_file=remote_file,
        payload=payload,
        remote_names=(remote_file,),
        declared_size_bytes=len(payload) + 1,
    )
    monkeypatch.setattr(
        level2_module, "_probe_control_connection", lambda endpoint: None
    )
    monkeypatch.setattr(
        level2_module,
        "_connect_download_session",
        lambda endpoint: ftp,
    )

    with pytest.raises(RuntimeError, match="FTP download size mismatch"):
        _build_level2_broker().fetch(
            record=DownloadPlan(
                source_name="sz_trade",
                trade_date="2026-07-20",
                broker="level2_ftp",
                raw_object="SZ_Trade",
            ),
            pm=PathManager(tmp_path),
        )

    assert ftp.is_closed is True


def test_level2_broker_accepts_a_control_timeout_after_the_full_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_file = "SZ_Trade.csv.7z"
    payload = b"source-native-level2"
    ftp = PayloadFtp(
        remote_file=remote_file,
        payload=payload,
        remote_names=(remote_file,),
        retrieval_error=TimeoutError("control response missing"),
    )
    logger = RecordingBrokerLogger()
    monkeypatch.setattr(
        level2_module, "_probe_control_connection", lambda endpoint: None
    )
    monkeypatch.setattr(
        level2_module,
        "_connect_download_session",
        lambda endpoint: ftp,
    )
    monkeypatch.setattr(level2_module, "logs", logger)

    downloaded_plan = _build_level2_broker().fetch(
        record=DownloadPlan(
            source_name="sz_trade",
            trade_date="2026-07-20",
            broker="level2_ftp",
            raw_object="SZ_Trade",
        ),
        pm=PathManager(tmp_path),
    )

    assert downloaded_plan is not None
    assert logger.warning_messages == [
        "⚠️ download; reason=control_response_timeout "
        "remote_file=SZ_Trade.csv.7z payload_complete=true"
    ]


def test_level2_broker_reports_an_empty_remote_directory_without_payload_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ftp = PayloadFtp(
        remote_file="unused.csv.7z",
        payload=b"unused",
        remote_names=(),
    )
    logger = RecordingBrokerLogger()
    monkeypatch.setattr(
        level2_module, "_probe_control_connection", lambda endpoint: None
    )
    monkeypatch.setattr(
        level2_module,
        "_connect_download_session",
        lambda endpoint: ftp,
    )
    monkeypatch.setattr(level2_module, "logs", logger)

    downloaded_plan = _build_level2_broker().fetch(
        record=DownloadPlan(
            source_name="sz_trade",
            trade_date="2026-07-20",
            broker="level2_ftp",
            raw_object="SZ_Trade",
        ),
        pm=PathManager(tmp_path),
    )

    assert downloaded_plan is None
    assert ftp.is_closed is True
    assert logger.warning_messages == [
        "⚠️ Level-2 remote directory; reason=empty trade_date=2026-07-20"
    ]
