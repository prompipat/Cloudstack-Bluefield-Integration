import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "compose.yaml"
FAKE_CLI = ROOT / "docker" / "fake-eswitchctl"


def test_dockerfile_runtime_contract() -> None:
    content = DOCKERFILE.read_text()

    assert "FROM python:3.12-slim-bookworm" in content
    assert "USER 10001:10001" in content
    assert "EXPOSE 8081" in content
    assert "HEALTHCHECK" in content
    assert "/health/ready" in content
    assert "COPY eswitchctl" not in content
    assert "COPY /usr/local/bin/eswitchctl" not in content


def test_compose_security_and_mount_contract() -> None:
    content = COMPOSE.read_text()

    required_fragments = (
        "platform: linux/arm64",
        "source: /usr/local/bin/eswitchctl",
        "target: /usr/local/bin/eswitchctl",
        "source: /run/eswitch-management",
        "target: /run/eswitch-management",
        'group_add:\n      - "0"',
        "cap_drop:\n      - ALL",
        "no-new-privileges:true",
        "read_only: true",
        "/tmp:size=16m,mode=1777",
    )
    for fragment in required_fragments:
        assert fragment in content

    assert "privileged:" not in content
    assert "/var/run/docker.sock" not in content


def run_fake(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [os.fspath(FAKE_CLI), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )


def test_fake_cli_help_and_status_are_non_mutating() -> None:
    help_result = run_fake("--help")
    status_result = run_fake("status")

    assert help_result.returncode == 0
    assert "Usage: eswitchctl" in help_result.stdout
    assert status_result.returncode == 0
    assert status_result.stdout.startswith("OK\n")
    assert "state=running" in status_result.stdout


def test_fake_cli_available_ports_cover_both_formats() -> None:
    result = run_fake("list-port-available")

    assert result.returncode == 0
    assert "DPDK port 0 (uplink/parent)" in result.stdout
    assert "DPDK port 1 (host=1 pf=0 vf=0)" in result.stdout


def test_fake_cli_rejects_mutation_commands() -> None:
    result = run_fake("vs-create", "--id", "1")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "rejects mutation commands" in result.stderr


def test_compose_is_independent_from_eswitch_daemon() -> None:
    content = COMPOSE.read_text()

    assert "\n  eswitch-management:" not in content
    assert "depends_on:" not in content
    assert "network_mode:" not in content
    assert "docker.sock" not in content
    assert "/dev/hugepages" not in content
    assert "/var/lib/eswitch-management" not in content
    assert "devices:" not in content


def test_compose_publishes_only_api_port_and_uses_readiness_healthcheck() -> None:
    compose = COMPOSE.read_text()
    dockerfile = DOCKERFILE.read_text()

    assert compose.count(":8081:8081") == 1
    assert "EXPOSE 8081" in dockerfile
    assert "/health/ready" in dockerfile
    assert "docker inspect" not in dockerfile


def test_image_and_entrypoint_do_not_manage_daemon_or_wait_for_socket() -> None:
    dockerfile = DOCKERFILE.read_text()
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text()
    combined = dockerfile + entrypoint

    forbidden = (
        "docker.sock",
        "docker start",
        "docker stop",
        "docker restart",
        "systemctl",
        "/dev/hugepages",
        "/var/lib/eswitch-management",
        "network_mode",
        "privileged",
        "vs-create",
        "vs-delete",
        "vs-port-attach",
        "vs-port-detach",
    )
    for fragment in forbidden:
        assert fragment not in combined

    assert "control.sock" not in entrypoint
    assert "eswitchctl" not in entrypoint
    assert "exec uvicorn integration_api.main:app" in entrypoint


def test_compose_mounts_only_required_host_resources() -> None:
    content = COMPOSE.read_text()

    assert content.count("source:") == 2
    assert content.count("read_only: true") == 3
    assert "source: /usr/local/bin/eswitchctl" in content
    assert "source: /run/eswitch-management" in content
