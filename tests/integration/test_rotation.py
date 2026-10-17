"""Explicit application-password rotation preserves roles and persistent data."""

import secrets
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values
from pymongo.errors import OperationFailure

from conf.db import connect, mongo_client
from conf.settings import DatabaseSettings
from tests.integration.stack import ComposeStack


def rotate(stack: ComposeStack, prefix: str) -> None:
    if prefix == "PG":
        stack.run(
            "exec",
            "-T",
            "postgres",
            "bash",
            "/docker-entrypoint-initdb.d/10-app.sh",
            "--rotate-app-password",
        )
    else:
        stack.run(
            "exec",
            "-T",
            "-e",
            "HW03_ROTATE_APP_PASSWORD=1",
            "mongodb",
            "mongosh",
            "--quiet",
            "--norc",
            "/docker-entrypoint-initdb.d/10-app.js",
        )


def write_password(path: Path, value: str) -> None:
    # Preserve the inode already bind-mounted by Compose secrets.
    path.chmod(0o600)
    try:
        path.write_text(value, encoding="utf-8")
    finally:
        path.chmod(0o444)


def verify_login_and_data(prefix: str, config: DatabaseSettings, marker: str) -> None:
    if prefix == "PG":
        with connect(config) as connection:
            assert connection.execute(
                "SELECT value FROM rotation_probe WHERE value = %s", (marker,)
            ).fetchone() == (marker,)
            assert connection.execute(
                "SELECT rolsuper, rolcreaterole FROM pg_roles "
                "WHERE rolname = current_user"
            ).fetchone() == (False, False)
    else:
        with mongo_client(config) as client:
            assert client[config.database].cats.find_one({"name": marker})
            with pytest.raises(OperationFailure) as error:
                client.admin.command("usersInfo")
            assert error.value.code == 13


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("PG", marks=pytest.mark.postgresql),
        pytest.param("MONGO", marks=pytest.mark.mongodb),
    ],
)
def test_password_rotation_on_existing_volume(stack: ComposeStack, prefix: str) -> None:
    old_config = stack.database_settings(prefix)
    marker = "rotation-" + secrets.token_hex(8)
    if prefix == "PG":
        with connect(old_config) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS rotation_probe (value TEXT)")
            connection.execute("INSERT INTO rotation_probe VALUES (%s)", (marker,))
    else:
        with mongo_client(old_config) as client:
            client[old_config.database].cats.insert_one({"name": marker, "age": 1})
    values = dotenv_values(stack.env_file, interpolate=False)
    secret_file = values[prefix + "_PASSWORD_FILE"]
    assert secret_file is not None
    path = Path(secret_file)
    original = path.read_text(encoding="utf-8")
    new_password = secrets.token_urlsafe(32) + " @:/?#%$&'\"\\ Ж"
    stack.remember_password(new_password)
    try:
        write_password(path, new_password + "\n")
        rotate(stack, prefix)
        rotate(stack, prefix)  # Reapplying the same password is safe.
        new_config = stack.database_settings(prefix)
        verify_login_and_data(prefix, new_config, marker)
        if prefix == "PG":
            with pytest.raises(psycopg.OperationalError):
                connect(replace(old_config, timeout=1))
        else:
            with mongo_client(replace(old_config, timeout=1)) as client:
                with pytest.raises(OperationFailure) as error:
                    client[old_config.database].cats.find_one({})
                assert error.value.code == 18
        service = "postgres" if prefix == "PG" else "mongodb"
        stack.run("restart", service)
        stack.start()
        verify_login_and_data(prefix, new_config, marker)
    finally:
        write_password(path, original)
        rotate(stack, prefix)
    verify_login_and_data(prefix, old_config, marker)
