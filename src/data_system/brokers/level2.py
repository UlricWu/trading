# filepath: src/data_system/brokers/level2.py
"""Level-2 raw-file broker for configured source-native payloads."""

from __future__ import annotations

import ftplib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, cast

from src import logs
from src.config.app_config import AppConfig
from src.config.data_config import DownloadBackend
from src.data_system.brokers.base import DownloadPlan
from src.utils.datetime_utils import DateTimeUtils
from src.utils.download_utils import DownloadProgress
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager

FTP_CONNECT_TIMEOUT_SECONDS = 15
FTP_SESSION_TIMEOUT_SECONDS = 1500
FTP_BLOCK_SIZE_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class FtpEndpoint:
    """Connection details for one FTP source, without broker semantics."""

    host: str
    port: int
    user: str
    password: str = field(repr=False)
    remote_root: str = ""

    def __post_init__(self) -> None:
        """Normalize the endpoint remote root for cwd, URL, and metadata use."""
        object.__setattr__(self, "remote_root", self.remote_root.strip("/"))


class Level2Broker:
    """
    Fetch one source-native Level-2 raw file from FTP.

    The adapter resolves one configured raw dataset to one vendor payload,
    downloads it to staging, copies it into the formal raw path, and returns a
    ``DownloadPlan`` for metadata commit. Archive handling follows
    ``docs/engineering/technology_stack_decisions.md``.

    Example:
        broker = Level2Broker(app_cfg=AppConfig.load())
    """

    name: ClassVar[str] = "level2_ftp"

    def __init__(self, *, app_cfg: AppConfig) -> None:
        """Load validated FTP settings and backend selection from AppConfig.

        Example:
            broker = Level2Broker(app_cfg=AppConfig.load())
        """
        broker_cfg = app_cfg.data.brokers[self.name]
        self._endpoint = FtpEndpoint(
            host=app_cfg.secret.ftp_host,
            port=app_cfg.secret.ftp_port,
            user=app_cfg.secret.ftp_user,
            password=app_cfg.secret.ftp_password,
            remote_root=cast(str, broker_cfg.remote_root),
        )
        self._backend = cast(DownloadBackend, broker_cfg.ftp_backend)

    def fetch(
        self,
        *,
        record: DownloadPlan,
        pm: PathManager,
    ) -> DownloadPlan | None:
        """
        Return one downloaded staging raw file for `raw_object` and `trade_date`.

        `raw_object` is the source-native object selected by `data.sources`;
        Missing remote date directories return `None`, while transport, auth,
        and ambiguous remote selection failures are allowed to fail.
        """
        trade_date = DateTimeUtils.require_system_date(
            record.trade_date,
            field_name="trade_date",
        )

        endpoint = self._endpoint

        _probe_control_connection(endpoint)
        ftp = _connect_download_session(endpoint)
        try:
            ftp.cwd(endpoint.remote_root)
            try:
                ftp.cwd(trade_date)
            except ftplib.error_perm as exc:
                if str(exc).startswith("550"):
                    return None
                raise

            names = ftp.nlst()

            if not names:
                logs.warning(
                    f"Level2 remote directory is empty; trade_date={trade_date}"
                )
                return None

            expected_file = f"{record.raw_object}.csv.7z"
            matches = [name for name in names if name == expected_file]

            if not matches:
                logs.warning(
                    f"Level2 remote file not found; file={expected_file} "
                    f"trade_date={trade_date} remote_file_count={len(names)}"
                )
                return None

            if len(matches) > 1:
                raise RuntimeError(
                    f"ambiguous Level2 remote files raw_object={record.raw_object!r} "
                    f"trade_date={trade_date!r} matches={matches!r}"
                )

            remote_file = matches[0]

            staging_file = pm.staging_payload(
                broker=record.broker,
                source_name=record.source_name,
                trade_date=trade_date,
                payload_file=remote_file,
            )

            remote_size = ftp.size(remote_file)
            if type(remote_size) is not int:
                raise RuntimeError(
                    f"FTP server did not return file size: {remote_file}"
                )
            if remote_size <= 0:
                raise ValueError(
                    f"FTP remote file size must be positive; "
                    f"file={remote_file} size_bytes={remote_size}"
                )

            logs.info(
                f"[Level2Broker] download_start "
                f"remote_size={FileSystem.format_size(remote_size)} "
                f"trade_date={trade_date} "
                f"file={remote_file} "
                f"backend={self._backend.value}"
            )

            self._download_to_staging(
                ftp=ftp,
                remote_file=remote_file,
                staging_file=staging_file,
                remote_size_bytes=remote_size,
            )
        finally:
            ftp.close()

        raw_path = pm.raw_payload(
            broker=record.broker,
            source_name=record.source_name,
            trade_date=trade_date,
            payload_file=remote_file,
        )

        FileSystem.copy_file_atomic(staging_file, raw_path)

        return DownloadPlan(
            source_name=record.source_name,
            trade_date=record.trade_date,
            broker=record.broker,
            raw_object=record.raw_object,
            payload_file=raw_path.name,
        )

    def _download_to_staging(
        self,
        *,
        ftp: ftplib.FTP,
        remote_file: str,
        staging_file: Path,
        remote_size_bytes: int,
    ) -> None:
        """Resume one FTP payload and atomically publish the completed staging file."""
        FileSystem.ensure_dir(staging_file.parent)
        part_file = staging_file.with_name(f"{staging_file.name}.part")
        if staging_file.is_symlink() or part_file.is_symlink():
            raise ValueError("FTP staging paths must not be symbolic links")

        progress = DownloadProgress(
            total_bytes=remote_size_bytes,
            filename=staging_file.name,
            logger=logs,
        )

        if staging_file.exists():
            staging_size_bytes = FileSystem.get_file_size(staging_file)
            if staging_size_bytes == remote_size_bytes:
                progress.update(remote_size_bytes)
                progress.finish()
                return
            if staging_size_bytes < remote_size_bytes and not part_file.exists():
                os.replace(staging_file, part_file)
            else:
                FileSystem.remove(staging_file)

        if part_file.exists():
            part_size_bytes = FileSystem.get_file_size(part_file)
            if part_size_bytes > remote_size_bytes:
                FileSystem.remove(part_file)

        resume_offset_bytes = (
            FileSystem.get_file_size(part_file) if part_file.exists() else 0
        )
        if resume_offset_bytes:
            progress.update(resume_offset_bytes)

        if resume_offset_bytes == remote_size_bytes:
            with part_file.open("rb") as completed_part_stream:
                os.fsync(completed_part_stream.fileno())
        else:
            file_mode = "ab" if resume_offset_bytes else "wb"
            try:
                with part_file.open(file_mode) as writable_part_stream:

                    def write_chunk(chunk: bytes) -> None:
                        writable_part_stream.write(chunk)
                        progress.update(len(chunk))

                    try:
                        ftp.retrbinary(
                            f"RETR {remote_file}",
                            write_chunk,
                            blocksize=FTP_BLOCK_SIZE_BYTES,
                            rest=resume_offset_bytes or None,
                        )
                    except TimeoutError:
                        writable_part_stream.flush()
                        os.fsync(writable_part_stream.fileno())
                        if FileSystem.get_file_size(part_file) != remote_size_bytes:
                            raise
                        logs.warning(
                            f"[FTP] control response timed out after full payload; "
                            f"remote_file={remote_file}"
                        )
                    else:
                        writable_part_stream.flush()
                        os.fsync(writable_part_stream.fileno())
            except BaseException:
                if part_file.exists() and FileSystem.get_file_size(part_file) == 0:
                    FileSystem.remove(part_file)
                raise

        actual_size_bytes = FileSystem.get_file_size(part_file)
        if actual_size_bytes != remote_size_bytes:
            raise RuntimeError(
                f"FTP download size mismatch; remote_file={remote_file} "
                f"expected_bytes={remote_size_bytes} actual_bytes={actual_size_bytes}"
            )

        os.replace(part_file, staging_file)
        directory_fd = os.open(staging_file.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        progress.finish()


def _probe_control_connection(endpoint: FtpEndpoint) -> None:
    ftp = ftplib.FTP(timeout=FTP_CONNECT_TIMEOUT_SECONDS)
    try:
        ftp.connect(endpoint.host, endpoint.port)
    except TimeoutError as exc:
        raise TimeoutError(
            "FTP control connection timed out "
            f"after {FTP_CONNECT_TIMEOUT_SECONDS}s; "
            "check local route, TUN, proxy, or firewall for <ftp_endpoint>"
        ) from exc
    finally:
        ftp.close()


def _connect_download_session(endpoint: FtpEndpoint) -> ftplib.FTP:
    ftp = ftplib.FTP(timeout=FTP_SESSION_TIMEOUT_SECONDS)
    try:
        ftp.connect(endpoint.host, endpoint.port)
        ftp.login(endpoint.user, endpoint.password)
    except BaseException:
        ftp.close()
        raise
    return ftp
