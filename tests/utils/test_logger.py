# filepath: tests/utils/test_logger.py
from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from loguru import logger as loguru_logger

import src.utils.logger as logger_module
from src import logs
from src.utils.logger import configure_cli_logging, configure_system_logging

_INFO_PREFIXES = ("▶️", "⏳", "✅", "♻️")
_STATUS_SYMBOLS = (*_INFO_PREFIXES, "⚠️", "❌")
_PREFIXES_BY_METHOD = {
    "trace": _INFO_PREFIXES,
    "debug": _INFO_PREFIXES,
    "info": _INFO_PREFIXES,
    "success": _INFO_PREFIXES,
    "warning": ("⚠️",),
    "error": ("❌",),
    "exception": ("❌",),
    "critical": ("❌",),
}


@pytest.fixture(autouse=True)
def restore_project_logger() -> Iterator[None]:
    """Give every test isolated sinks and restore stderr afterward."""
    logs.remove()
    logs.add(sys.stderr)
    try:
        yield
    finally:
        logs.complete()
        logs.remove()
        logs.add(sys.stderr)


def test_logger_module_exports_current_public_contract() -> None:
    assert logger_module.__all__ == [
        "ProcessLogger",
        "configure_cli_logging",
        "configure_system_logging",
        "logs",
    ]
    assert logs is loguru_logger
    assert logger_module.logs is logs


def test_src_log_messages_start_with_level_compatible_status_symbols() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []

    for source_file in sorted((repository_root / "src").rglob("*.py")):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func,
                ast.Attribute,
            ):
                continue
            allowed_prefixes = _PREFIXES_BY_METHOD.get(node.func.attr)
            if allowed_prefixes is None or not _is_project_logger(node.func.value):
                continue
            message_prefix = _static_message_prefix(node)
            allowed_starts = tuple(f"{prefix} " for prefix in allowed_prefixes)
            symbol_count = (
                0
                if message_prefix is None
                else sum(message_prefix.count(symbol) for symbol in _STATUS_SYMBOLS)
            )
            if (
                message_prefix is None
                or not message_prefix.startswith(allowed_starts)
                or symbol_count != 1
            ):
                relative_path = source_file.relative_to(repository_root)
                violations.append(
                    f"{relative_path}:{node.lineno} method={node.func.attr} "
                    f"prefix={message_prefix!r}"
                )

    assert violations == []


def _is_project_logger(expression: ast.expr) -> bool:
    if isinstance(expression, ast.Name):
        return expression.id == "logs" or expression.id.endswith("logger")
    if isinstance(expression, ast.Attribute):
        return expression.attr == "logs" or expression.attr.endswith("logger")
    if isinstance(expression, ast.Call) and isinstance(
        expression.func,
        ast.Attribute,
    ):
        return expression.func.attr == "opt" and _is_project_logger(
            expression.func.value
        )
    return False


def _static_message_prefix(node: ast.Call) -> str | None:
    if not node.args:
        return None
    message = node.args[0]
    if isinstance(message, ast.Constant) and isinstance(message.value, str):
        return message.value
    if isinstance(message, ast.JoinedStr) and message.values:
        first_part = message.values[0]
        if isinstance(first_part, ast.Constant) and isinstance(first_part.value, str):
            return first_part.value
    return None


def test_configure_system_logging_requires_path() -> None:
    invalid_path = cast(Path, "logs/system/service.log")

    with pytest.raises(TypeError, match="system_log_file must be a pathlib.Path"):
        configure_system_logging(invalid_path)


def test_configure_cli_logging_uses_module_and_line_without_function(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_cli_logging()
    run_id = "cli-1"

    logs.debug(f"debug run_id={run_id}")
    logs.info(f"test run_id={run_id}")

    stderr = capsys.readouterr().err
    assert f"debug run_id={run_id}" not in stderr
    assert re.search(
        r" \| INFO     \| tests\.utils\.test_logger:\d+ - test run_id=cli-1\n$",
        stderr,
    )
    assert (
        ":test_configure_cli_logging_uses_module_and_line_without_function:"
        not in stderr
    )


def test_configure_system_logging_routes_to_new_file_and_stderr(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    replaced_sink_messages: list[str] = []
    logs.add(replaced_sink_messages.append, format="{message}")
    system_log_file = tmp_path / "logs" / "system" / "2026-07-22-09-15-32.123456.log"

    configure_system_logging(system_log_file)
    run_id = "service-1"
    logs.info(f"test run_id={run_id}")
    logs.complete()

    assert list(system_log_file.parent.iterdir()) == [system_log_file]
    assert "test run_id=service-1" in system_log_file.read_text(
        encoding="utf-8"
    )
    assert "test run_id=service-1" in capsys.readouterr().err
    assert replaced_sink_messages == []


def test_configure_system_logging_rejects_existing_run_file(
    tmp_path: Path,
) -> None:
    system_log_file = tmp_path / "system" / "2026-07-22-09-15-32.123456.log"
    system_log_file.parent.mkdir(parents=True)
    system_log_file.write_text("previous service run\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        configure_system_logging(system_log_file)

    assert system_log_file.read_text(encoding="utf-8") == "previous service run\n"
