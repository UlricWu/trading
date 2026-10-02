# filepath: tests/data_system/brokers/test_level2.py
"""Exercise the Baidu CLI boundary through a real local subprocess substitute."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from src.access.meta import RemoteRawRecord
from src.config.app_config import AppConfig
from src.config.data_config import BrokerConfig, DataConfig
from src.data_system.brokers.level2 import Level2Broker


@pytest.fixture
def broker(tmp_path: Path) -> Level2Broker:
    executable = tmp_path / "pcs"
    executable.write_text(f"#!{sys.executable}\n" + '''
import json
import sys
from pathlib import Path
root = Path(__file__).parent
args = sys.argv[1:]
with (root / "calls").open("a") as output:
    output.write(json.dumps(args) + "\\n")
spec = json.loads((root / "spec.json").read_text())
if args[0] == "meta":
    size = spec["files"].get(args[1])
    if size is None:
        print("获取文件/目录的元信息: 代码: 31066, 消息: 文件或目录不存在")
    elif size == "auth_error":
        print("认证失败: do-not-expose-token")
    else:
        print(f"  类型              文件\\n  文件路径          {args[1]}\\n  文件大小          {size}, 1GB")
else:
    destination = Path(args[args.index("--saveto") + 1]) / Path(args[-1]).name
    mode = spec.get("mode", "success")
    state = destination.with_name(destination.name + ".BaiduPCS-Go-downloading")
    destination.write_bytes(b"raw" if mode != "wrong_size" else b"wrong")
    if mode in ("failure", "state"):
        state.write_text("unfinished ranges")
    else:
        state.unlink(missing_ok=True)
    if mode != "failure":
        print(f"[1] 下载完成, 保存位置: {destination}")
    else:
        print("download failed: do-not-expose-token")
''')
    executable.chmod(0o700)
    (tmp_path / "spec.json").write_text(json.dumps({"files": {"/level2/2026-09-21/SZ_Trade.csv.7z": 3}}))
    return Level2Broker(app_cfg=AppConfig.model_construct(data=DataConfig(brokers={
        "level2_ftp": BrokerConfig(
            baidupcs_go=str(executable), raw_cache_days=5,
            remote_path_templates=("/level2/{date}-New/{file}", "/level2/{date}/{file}"),
        ),
    })))


def test_locate_prefers_new_but_migration_requires_original_size(broker: Level2Broker, tmp_path: Path) -> None:
    new = "/level2/2026-09-21-New/SZ_Trade.csv.7z"
    old = "/level2/2026-09-21/SZ_Trade.csv.7z"
    (tmp_path / "spec.json").write_text(json.dumps({"files": {new: 5, old: 3}}))
    assert broker.locate(raw_object="SZ_Trade", trade_date="2026-09-21") == RemoteRawRecord("SZ_Trade.csv.7z", 5, new)
    assert broker.locate(raw_object="SZ_Trade", trade_date="2026-09-21", expected_size=3) == RemoteRawRecord("SZ_Trade.csv.7z", 3, old)
    assert broker.locate(raw_object="SZ_Trade", trade_date="2026-09-21", expected_size=8) is None
    assert broker.locate(raw_object="SZ_Order", trade_date="2026-09-21") is None


def test_cli_zero_exit_auth_error_is_not_missing(broker: Level2Broker, tmp_path: Path) -> None:
    path = "/level2/2026-09-21/SZ_Trade.csv.7z"
    (tmp_path / "spec.json").write_text(json.dumps({"files": {path: "auth_error"}}))
    with pytest.raises(RuntimeError, match="metadata unavailable") as error:
        broker.describe(path)
    assert "do-not-expose-token" not in str(error.value)


def test_download_resumes_then_publishes_without_hashing(broker: Level2Broker, tmp_path: Path) -> None:
    record = broker.locate(raw_object="SZ_Trade", trade_date="2026-09-21")
    assert record is not None
    path = tmp_path / "cache" / record.payload
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"files": {record.remote_path: 3}, "mode": "failure"}))
    with pytest.raises(RuntimeError, match="incomplete") as error:
        broker.download(record=record, destination=path)
    assert "do-not-expose-token" not in str(error.value)
    assert not path.exists()
    partial = path.parent / ".download" / record.payload
    assert partial.stat().st_size == 3
    spec.write_text(json.dumps({"files": {record.remote_path: 3}}))
    assert broker.download(record=record, destination=path) == path
    assert path.read_bytes() == b"raw"
    assert not partial.exists()
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    downloads = [args for args in calls if args[0] == "download"]
    assert len(downloads) == 2
    assert all("--nocheck" in args and "--ow" in args for args in downloads)
    (tmp_path / "calls").unlink()
    assert broker.download(record=record, destination=path) == path
    assert not (tmp_path / "calls").exists()


@pytest.mark.parametrize("mode", ["state", "wrong_size"])
def test_size_or_residual_state_prevents_publish(broker: Level2Broker, tmp_path: Path, mode: str) -> None:
    record = broker.locate(raw_object="SZ_Trade", trade_date="2026-09-21")
    assert record is not None
    (tmp_path / "spec.json").write_text(json.dumps({"files": {record.remote_path: 3}, "mode": mode}))
    path = tmp_path / "cache" / record.payload
    with pytest.raises(RuntimeError):
        broker.download(record=record, destination=path)
    assert not path.exists()


def test_changed_cloud_size_prevents_download(broker: Level2Broker, tmp_path: Path) -> None:
    record = RemoteRawRecord("SZ_Trade.csv.7z", 4, "/level2/2026-09-21/SZ_Trade.csv.7z")
    with pytest.raises(RuntimeError, match="size changed"):
        broker.download(record=record, destination=tmp_path / record.payload)
    assert all(json.loads(line)[0] == "meta" for line in (tmp_path / "calls").read_text().splitlines())
