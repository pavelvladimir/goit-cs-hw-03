"""Pinned server versions, persistence and resource cleanup."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conf.db import connect, mongo_client
from conf.settings import ROOT
from create_tables import create_tables
from tests.integration.stack import ComposeStack, clean_environment

DATABASES = [
    pytest.param("PG", marks=pytest.mark.postgresql),
    pytest.param("MONGO", marks=pytest.mark.mongodb),
]


@pytest.mark.parametrize("prefix", DATABASES)
def test_pinned_server_versions(stack: ComposeStack, prefix: str) -> None:
    config = stack.database_settings(prefix)
    if prefix == "PG":
        with connect(config) as connection:
            version = connection.execute("SHOW server_version").fetchone()
            assert version is not None and str(version[0]).startswith("17.11")
            assert connection.execute("SHOW server_encoding").fetchone() == ("UTF8",)
    else:
        with mongo_client(config) as client:
            assert str(client.server_info()["version"]).startswith("8.0.32")


@pytest.mark.parametrize("prefix", DATABASES)
def test_data_survives_container_restart(stack: ComposeStack, prefix: str) -> None:
    config = stack.database_settings(prefix)
    if prefix == "PG":
        with connect(config) as connection:
            create_tables(connection)
            connection.execute(
                "INSERT INTO users (fullname, email) VALUES (%s, %s)",
                ("Persistence", "persistence@example.com"),
            )
    else:
        with mongo_client(config) as client:
            client[config.database].cats.insert_one({"name": "persistent", "age": 1})
    stack.run("restart", *stack.services)
    stack.start()
    if prefix == "PG":
        with connect(config) as connection:
            assert connection.execute(
                "SELECT fullname FROM users WHERE email = %s",
                ("persistence@example.com",),
            ).fetchone() == ("Persistence",)
    else:
        with mongo_client(config) as client:
            assert (
                client[config.database].cats.find_one({"name": "persistent"})
                is not None
            )


def test_cleanup_after_intentional_pytest_failure(tmp_path: Path) -> None:
    # Run a real failing pytest process: fixture teardown must still remove all mounts.
    manifest = tmp_path / "resources.json"
    child_test = tmp_path / "test_child.py"
    child_test.write_text(CHILD_TEST, encoding="utf-8")
    environment = clean_environment() | {"HW03_CLEANUP_MANIFEST": str(manifest)}
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "tests.reporting",
                "-c",
                str(ROOT / "pyproject.toml"),
                "-o",
                "addopts=",
                "--basetemp",
                str(tmp_path / "pytest-state"),
                str(child_test),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=720,
        )
        assert result.returncode == 1
        assert "intentional pytest failure" in result.stdout
        resources = json.loads(manifest.read_text())
        assert resources["volumes"]
        # Image-declared anonymous mounts have no Compose label. Check their names too.
        volumes = docker_inventory(environment, "volume")
        assert not set(resources["volumes"]) & volumes
        label = "label=com.docker.compose.project=" + resources["project"]
        for resource in ("container", "volume", "network"):
            assert not docker_inventory(environment, resource, label)
        assert not Path(resources["env_file"]).exists()
        assert not (Path(resources["env_file"]).parent / "secrets").exists()
    finally:
        if manifest.exists():
            resources = json.loads(manifest.read_text())
            # A timeout or failed assertion must not leak the child's unique stack.
            if Path(resources["env_file"]).exists():
                recovery = ComposeStack(
                    resources["project"],
                    Path(resources["env_file"]),
                    resources["command"],
                    clean_environment(),
                    (),
                )
                recovery.run("down", "--volumes", "--remove-orphans")
                recovery.cleanup_files()


def docker_inventory(
    environment: dict[str, str], resource: str, label: str | None = None
) -> set[str]:
    command = ["docker", resource, "ls", "--quiet"]
    if resource == "container":
        command.append("--all")
    if label:
        command.extend(["--filter", label])
    result = subprocess.run(
        command, env=environment, capture_output=True, text=True, timeout=15, check=True
    )
    return set(result.stdout.split())


CHILD_TEST = r"""import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.integration.stack import ComposeStack, running_stack

MANIFEST = Path(os.environ["HW03_CLEANUP_MANIFEST"])


@pytest.fixture
def own_stack(tmp_path):
    instance = ComposeStack.create(tmp_path)
    resources = {
        "project": instance.project, "env_file": str(instance.env_file),
        "command": instance.command, "volumes": [],
    }
    MANIFEST.write_text(json.dumps(resources))
    with running_stack(instance):
        containers = instance.run("ps", "--all", "--quiet").split()
        assert containers
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .Mounts}}", *containers],
            env=instance.environment, capture_output=True, text=True,
            timeout=15, check=True,
        )
        mounts = [
            mount for line in result.stdout.splitlines()
            for mount in json.loads(line) if mount["Type"] == "volume"
        ]
        resources["volumes"] = sorted({mount["Name"] for mount in mounts})
        assert any(mount["Destination"] == "/pgdata" for mount in mounts)
        MANIFEST.write_text(json.dumps(resources))
        yield instance


def test_intentional_failure(own_stack):
    assert False, "intentional pytest failure"
"""
