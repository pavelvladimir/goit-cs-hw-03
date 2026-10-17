"""Real port collisions must clean partially created containers and every mount."""

import json
import socket
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values

from tests.integration.stack import ComposeStack, running_stack
from tests.integration.test_lifecycle import docker_inventory


@pytest.mark.parametrize(
    "partial", [pytest.param(False, marks=pytest.mark.postgresql), True]
)
def test_real_startup_failure_cleans_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, partial: bool
) -> None:
    directory = tmp_path / "Частковий запуск O'Brien"
    directory.mkdir()
    stack = ComposeStack.create(
        directory, ("postgres", "mongodb") if partial else ("postgres",)
    )
    values = dotenv_values(stack.env_file, interpolate=False)
    port = values["HW03_MONGO_TEST_PORT" if partial else "HW03_PG_TEST_PORT"]
    assert port is not None
    volumes: set[str] = set()
    label = "label=com.docker.compose.project=" + stack.project
    original_start = stack.start

    def start_and_inventory() -> None:
        try:
            original_start()
        finally:
            ids = stack.run("ps", "--all", "--quiet").split()
            if ids:
                result = subprocess.run(
                    ["docker", "inspect", *ids],
                    env=stack.environment,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                )
                for container in json.loads(result.stdout):
                    volumes.update(
                        mount["Name"]
                        for mount in container["Mounts"]
                        if mount["Type"] == "volume"
                    )

    monkeypatch.setattr(stack, "start", start_and_inventory)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", int(port)))
        occupied.listen()
        try:
            if partial:
                # A real healthy service exists before the second service fails to bind.
                stack.run("up", "-d", "--wait", "postgres")
                assert (
                    stack.run("ps", "--status", "running", "--services").strip()
                    == "postgres"
                )
            with pytest.raises(RuntimeError, match="Compose up failed"):
                with running_stack(stack):
                    pytest.fail("Startup unexpectedly accepted an occupied host port")
            assert not stack.env_file.exists()
            assert not (directory / "secrets").exists()
            for resource in ("container", "volume", "network"):
                assert not docker_inventory(stack.environment, resource, label)
            assert not volumes & docker_inventory(stack.environment, "volume")
            # Cleanup must leave the unrelated owner of the port alone.
            assert occupied.getsockname()[1] == int(port)
        finally:
            if stack.env_file.exists():
                stack.run("down", "--volumes", "--remove-orphans")
                stack.cleanup_files()
