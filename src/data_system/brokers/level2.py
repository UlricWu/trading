# filepath: src/data_system/brokers/level2.py
"""Baidu Netdisk transport for source-native Level-2 archives."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import ClassVar, cast

from src import logs
from src.access.meta import RemoteRawRecord
from src.config.app_config import AppConfig
from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


class Level2Broker:
    """Locate cloud raw objects and download their compressed working cache.

    Example:
        broker = Level2Broker(app_cfg=AppConfig.load())
        record = broker.locate(raw_object="SZ_Trade", trade_date="2026-09-21")
    """

    name: ClassVar[str] = "level2_ftp"

    def __init__(self, *, app_cfg: AppConfig) -> None:
        """Bind file-backed settings; the CLI owns its already logged-in account.

        Example:
            broker = Level2Broker(app_cfg=app_config)
        """
        config = app_cfg.data.brokers[self.name]
        self._executable = cast(str, config.baidupcs_go)
        self._templates = cast(tuple[str, ...], config.remote_path_templates)

    def describe(self, remote_path: str) -> RemoteRawRecord | None:
        """Read an exact cloud path; only error 31066 represents absence.

        Example:
            record = broker.describe("/level2/2026-09-21/SZ_Trade.csv.7z")
        """
        result = subprocess.run(
            [self._executable, "meta", remote_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", timeout=60, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Baidu metadata command failed: path={remote_path}")
        output = result.stdout
        if re.search(r"代码:\s*31066\s*,", output):
            return None
        path = re.search(r"^\s*文件路径\s+(.+?)\s*$", output, re.MULTILINE)
        size = re.search(r"^\s*文件大小\s+(\d+),", output, re.MULTILINE)
        kind = re.search(r"^\s*类型\s+文件\s*$", output, re.MULTILINE)
        if not path or path[1] != remote_path or not size or not kind:
            raise RuntimeError(f"Baidu metadata unavailable or invalid: path={remote_path}")
        size_bytes = int(size[1])
        if size_bytes <= 0:
            raise RuntimeError(f"Baidu raw size must be positive: path={remote_path}")
        return RemoteRawRecord(
            payload=PurePosixPath(remote_path).name,
            size_bytes=size_bytes, remote_path=remote_path,
        )

    def locate(
        self, *, raw_object: str, trade_date: str, expected_size: int | None = None,
    ) -> RemoteRawRecord | None:
        """Select the first existing candidate, or the first matching legacy size.

        Example:
            record = broker.locate(
                raw_object="SZ_Trade", trade_date="2026-09-21", expected_size=123,
            )
        """
        DateTimeUtils.require_system_date(trade_date, field_name="trade_date")
        PathManager.require_safe_basename(raw_object, "raw_object")
        for template in self._templates:
            record = self.describe(template.format(
                month=trade_date[:7], date=trade_date, file=f"{raw_object}.csv.7z",
            ))
            if record is not None and (
                expected_size is None or record.size_bytes == expected_size
            ):
                return record
        return None

    def download(self, *, record: RemoteRawRecord, destination: Path) -> Path:
        """Resume into a private directory and publish a completed size-matched cache.

        The caller holds the Level-2 cache lock through normalization.

        Example:
            input_file = broker.download(record=remote, destination=cache_path)
        """
        if destination.is_symlink():
            raise RuntimeError(f"Level-2 cache must not be a symlink: {destination}")
        if destination.exists() and FileSystem.get_file_size(destination) == record.size_bytes:
            return destination
        current = self.describe(record.remote_path)
        if current != record:
            raise RuntimeError(f"Baidu raw missing or size changed: path={record.remote_path}")
        partial_dir = FileSystem.ensure_dir(destination.parent / ".download")
        partial = partial_dir / record.payload
        state = partial.with_name(partial.name + ".BaiduPCS-Go-downloading")
        if partial_dir.is_symlink() or partial.is_symlink() or state.is_symlink():
            raise RuntimeError(f"Level-2 download paths must not be symlinks: {partial_dir}")
        logs.info(
            f"▶️ Baidu download; file={record.payload} size_bytes={record.size_bytes} "
            f"destination={destination}"
        )
        command = [
            self._executable, "download", "--saveto", str(partial_dir),
            "--nocheck", "--ow", "-p", "20", "-l", "1", record.remote_path,
        ]
        completed = False
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8",
        ) as process:
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    if line.rstrip().endswith(f"下载完成, 保存位置: {partial}"):
                        completed = True
                returncode = process.wait()
            except BaseException:
                process.kill()
                process.wait()
                raise
        if returncode != 0 or not completed or state.exists():
            raise RuntimeError(f"Baidu download incomplete; partial retained: {partial}")
        if FileSystem.get_file_size(partial) != record.size_bytes:
            raise RuntimeError(f"Baidu download size mismatch; partial retained: {partial}")
        with partial.open("rb") as payload:
            os.fsync(payload.fileno())
        os.replace(partial, destination)
        logs.info(f"✅ Baidu cache published; file={destination}")
        return destination
