"""Configuration validation and explicit driver parameters."""

from pathlib import Path
from unittest.mock import patch

import pytest
from dotenv import dotenv_values

from conf import db
from conf.settings import (
    ConfigurationError,
    from_values,
    identifier,
    read_secret,
    secret_path,
    settings,
)


def test_environment_priority_and_secret_characters(
    local_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = "p@ss:/?#%$&'\"\\ word"
    secret = local_config / "secrets/pg_password"
    secret.chmod(0o600)
    secret.write_text(value + "\n")
    monkeypatch.setenv("HW03_ENV_FILE", str(local_config / ".env"))
    monkeypatch.setenv("PG_PORT", "6543")
    config = settings("PG")
    assert config.port == 6543
    assert config.password == value
    assert value not in repr(config)
    with patch("conf.db.psycopg.connect") as driver:
        db.connect()
        assert driver.call_args.kwargs["password"] == value
        assert not driver.call_args.args
        assert (
            driver.call_args.kwargs["options"]
            == "-c statement_timeout=10000 -c lock_timeout=10000"
        )
    with patch.object(db, "MongoClient") as mongo:
        db.mongo_client(config)
        assert mongo.call_args.kwargs["password"] == value
        assert mongo.call_args.kwargs["authSource"] == config.database
        assert mongo.call_args.kwargs["timeoutMS"] == 10000


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PG_USER", "postgres"),
        ("PG_USER", "pg_admin"),
        ("PG_USER", "public"),
        ("PG_USER", "none"),
        ("PG_DB", "template0"),
        ("PG_DB", "template1"),
        ("PG_DB", "bad;sql"),
        ("PG_DB", ""),
        ("PG_PORT", "0"),
        ("PG_PORT", "65536"),
        ("PG_PORT", "oops"),
        ("DB_TIMEOUT", "31"),
        ("DB_QUERY_TIMEOUT", "0"),
        ("DB_QUERY_TIMEOUT", "301"),
        ("DB_QUERY_TIMEOUT", "bad"),
        ("PG_HOST", "host/password"),
        ("PG_PASSWORD", "must-not-leak"),
        ("PG_PASSWORD_FILE", ""),
    ],
)
def test_invalid_settings_are_safe(
    local_config: Path, monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    monkeypatch.setenv(key, value)
    with pytest.raises(ConfigurationError) as error:
        settings("PG", local_config / ".env")
    assert "must-not-leak" not in str(error.value)
    assert key in str(error.value) or key == "PG_PASSWORD"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PG_DB", "public"),
        ("PG_DB", "none"),
        ("PG_USER", "template0"),
        ("MONGO_USER", "public"),
        ("MONGO_USER", "none"),
        ("MONGO_DB", "template1"),
    ],
)
def test_postgres_reserved_names_are_scoped_to_the_field(key: str, value: str) -> None:
    assert identifier({key: value}, key) == value


def test_explicit_configuration_ignores_shell(
    local_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PG_DB", "developer_database")
    values = {k: v for k, v in dotenv_values(local_config / ".env").items() if v}
    assert from_values("PG", values, local_config).database == "task_manager"
    with pytest.raises(ConfigurationError):
        from_values("OTHER", values, local_config)
    assert secret_path({"FILE": "/tmp/test"}, "FILE", local_config) == Path("/tmp/test")


@pytest.mark.parametrize("content", ["", " ", "one\ntwo", "a\r", "a\0", "a\n\n"])
def test_invalid_secret_contents(tmp_path: Path, content: str) -> None:
    path = tmp_path / "secret"
    path.write_text(content)
    with pytest.raises(ConfigurationError):
        read_secret(path)


def test_missing_and_non_utf8_secrets(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        read_secret(tmp_path / "missing")
    path = tmp_path / "secret"
    path.write_bytes(b"\xff")
    with pytest.raises(ConfigurationError):
        read_secret(path)
