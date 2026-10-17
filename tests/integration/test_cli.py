"""Real command line processes against the isolated databases."""

import socket
import subprocess
import sys
from pathlib import Path

import pytest

from conf.db import connect
from conf.settings import ROOT
from create_tables import create_tables
from tests.integration.stack import ComposeStack


@pytest.mark.parametrize(
    ("script", "args", "expected"),
    [
        pytest.param("create_tables.py", [], "створено", marks=pytest.mark.postgresql),
        pytest.param("seed.py", [], "Додано 10", marks=pytest.mark.postgresql),
        pytest.param(
            "main.py", ["--seed"], "Приклади котів додано", marks=pytest.mark.mongodb
        ),
        pytest.param("main.py", [], "Показати всіх котів", marks=pytest.mark.mongodb),
    ],
)
def test_cli_success_from_another_directory(
    stack: ComposeStack, tmp_path: Path, script: str, args: list[str], expected: str
) -> None:
    if script == "seed.py":
        with connect(stack.database_settings("PG")) as connection:
            create_tables(connection)
    result = subprocess.run(
        [sys.executable, str(ROOT / script), *args],
        cwd=tmp_path,
        env=stack.app_environment(),
        input="1\n0\n",
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize(
    "script",
    [
        pytest.param("create_tables.py", marks=pytest.mark.postgresql),
        pytest.param("seed.py", marks=pytest.mark.postgresql),
        pytest.param("main.py", marks=pytest.mark.mongodb),
    ],
)
@pytest.mark.parametrize("failure", ["authentication", "unavailable"])
def test_cli_database_failure_is_safe(
    stack: ComposeStack, tmp_path: Path, script: str, failure: str
) -> None:
    env = stack.app_environment() | {"DB_TIMEOUT": "1"}
    secret = tmp_path / "wrong_password"
    secret.write_text("must-not-appear-in-errors")
    # Reserve a local port with no database listener; never probe arbitrary services.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        if failure == "authentication":
            env |= {"PG_PASSWORD_FILE": str(secret), "MONGO_PASSWORD_FILE": str(secret)}
        else:
            port = str(reserved.getsockname()[1])
            env |= {"PG_PORT": port, "MONGO_PORT": port}
        result = subprocess.run(
            [sys.executable, str(ROOT / script)],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    if failure == "authentication":
        expected = {"AUTH"} if script == "main.py" else {"AUTH", "CONNECTION"}
    else:
        # OS networking may refuse immediately or expire the connection deadline.
        expected = {"CONNECTION", "TIMEOUT"}
    assert any(f"[{category}]" in result.stderr for category in expected)
    for password in (*stack.passwords, "must-not-appear-in-errors"):
        assert password not in result.stderr
