# filepath: tests/scripts/test_deploy_local_machine.py
from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_RELAY = REPO_ROOT / "scripts" / "deploy_local_machine.sh"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _relay_environment(tmp_path: Path) -> dict[str, str]:
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("test-host ssh-ed25519 test-key\n", encoding="utf-8")
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    _write_executable(command_dir / "flock", "#!/usr/bin/env bash\nexit 0\n")

    ssh_capture = tmp_path / "ssh-arguments"
    _write_executable(
        command_dir / "ssh",
        """#!/usr/bin/env bash
printf '%s\n' "$@" >"$SSH_CAPTURE"
exit "${SSH_EXIT_CODE:-0}"
""",
    )
    return {
        **os.environ,
        "PATH": f"{command_dir}:{os.environ['PATH']}",
        "DEPLOY_DIR": str(deploy_dir),
        "DEPLOY_LOG": str(deploy_dir / "relay.log"),
        "DEPLOY_LOCK_FILE": str(deploy_dir / "relay.lock"),
        "SSH_KNOWN_HOSTS": str(known_hosts),
        "TRAINING_MACHINE": "deployer@test-host",
        "RUN_ID": "delivery-1234",
        "DEPLOY_SHA": COMMIT_SHA,
        "REMOTE_SCRIPT": "/srv/minquant/deploy/deploy_release.sh",
        "SSH_CAPTURE": str(ssh_capture),
        "SSH_EXIT_CODE": "0",
    }


def test_relay_rejects_a_missing_commit_sha_before_ssh(tmp_path: Path) -> None:
    environment = _relay_environment(tmp_path)
    del environment["DEPLOY_SHA"]

    completed = subprocess.run(
        ["bash", str(DEPLOY_RELAY)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 64
    assert "DEPLOY_SHA must be one full lowercase commit SHA" in completed.stderr
    assert not Path(environment["SSH_CAPTURE"]).exists()


def test_relay_requires_an_explicit_remote_script(tmp_path: Path) -> None:
    environment = _relay_environment(tmp_path)
    del environment["REMOTE_SCRIPT"]

    completed = subprocess.run(
        ["bash", str(DEPLOY_RELAY)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 64
    assert "REMOTE_SCRIPT must be a safe absolute path" in completed.stderr
    assert not Path(environment["SSH_CAPTURE"]).exists()


def test_relay_returns_success_after_remote_deployment_success(
    tmp_path: Path,
) -> None:
    environment = _relay_environment(tmp_path)

    completed = subprocess.run(
        ["bash", str(DEPLOY_RELAY)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    relay_log = Path(environment["DEPLOY_LOG"]).read_text(encoding="utf-8")
    assert "remote deployment succeeded" in relay_log
    assert "deployment relay completed" in relay_log


def test_relay_pins_host_identity_and_propagates_ssh_failure(
    tmp_path: Path,
) -> None:
    environment = _relay_environment(tmp_path)
    environment["SSH_EXIT_CODE"] = "23"

    completed = subprocess.run(
        ["bash", str(DEPLOY_RELAY)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 23
    ssh_arguments = Path(environment["SSH_CAPTURE"]).read_text(
        encoding="utf-8"
    ).splitlines()
    assert "StrictHostKeyChecking=yes" in ssh_arguments
    assert f"UserKnownHostsFile={environment['SSH_KNOWN_HOSTS']}" in ssh_arguments
    assert "deployer@test-host" in ssh_arguments
    assert any(f"RUN_ID=delivery-1234" in argument for argument in ssh_arguments)
    assert any(f"DEPLOY_SHA={COMMIT_SHA}" in argument for argument in ssh_arguments)

    relay_log = Path(environment["DEPLOY_LOG"]).read_text(encoding="utf-8")
    assert "remote deployment failed" in relay_log
    assert "exit_code=23" in relay_log
