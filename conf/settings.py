"""Validated configuration; environment overrides a selected dotenv file."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SECRET_KEYS = (
    "PG_PASSWORD_FILE",
    "PG_ADMIN_PASSWORD_FILE",
    "MONGO_PASSWORD_FILE",
    "MONGO_ADMIN_PASSWORD_FILE",
)


class ConfigurationError(ValueError):
    """A configuration error whose message contains field names, never values."""


def environment_file() -> Path:
    return Path(os.environ.get("HW03_ENV_FILE", ROOT / ".env")).resolve()


def read_values(path: Path | None = None) -> dict[str, str]:
    source = path if path is not None else environment_file()
    try:
        values = dotenv_values(source, interpolate=False)
    except OSError, UnicodeError:
        raise ConfigurationError("Не вдалося прочитати файл конфігурації.") from None
    return {key: value for key, value in values.items() if value is not None} | dict(
        os.environ
    )


def identifier(values: Mapping[str, str], key: str) -> str:
    value = values.get(key, "")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value):
        raise ConfigurationError(f"{key}: потрібен коректний ідентифікатор.")
    reserved = {"postgres", "admin", "local", "config", "root"}
    if key == "PG_USER":
        reserved.update(("public", "none"))
    elif key == "PG_DB":
        reserved.update(("template0", "template1"))
    if value in reserved or value.startswith("pg_"):
        raise ConfigurationError(f"{key}: потрібне окреме ім'я для застосунку.")
    return value


def number(values: Mapping[str, str], key: str, default: int, maximum: int) -> int:
    try:
        value = int(values.get(key, str(default)))
    except ValueError:
        raise ConfigurationError(f"{key}: потрібне ціле число.") from None
    if not 1 <= value <= maximum:
        raise ConfigurationError(f"{key}: значення поза допустимими межами.")
    return value


def host(values: Mapping[str, str], key: str) -> str:
    value = values.get(key, "127.0.0.1")
    if not re.fullmatch(r"[a-zA-Z0-9_.:\-]+", value):
        raise ConfigurationError(f"{key}: потрібне ім'я хоста або IP-адреса.")
    return value


def read_secret(path: Path) -> str:
    try:
        if path.is_symlink() or not path.is_file():
            raise ConfigurationError("Секрет має бути звичайним файлом.")
        value = path.read_bytes().decode("utf-8").removesuffix("\n")
    except OSError, UnicodeError:
        raise ConfigurationError("Не вдалося прочитати файл секрету.") from None
    if not value.strip() or any(char in value for char in "\r\n\0"):
        raise ConfigurationError("Секрет має бути непорожнім однорядковим значенням.")
    return value


def secret_path(values: Mapping[str, str], key: str, base: Path) -> Path:
    value = values.get(key, "")
    if not value or any(char in value for char in "\r\n\0$"):
        raise ConfigurationError(f"{key}: потрібен шлях до файлу секрету.")
    path = Path(value)
    return path if path.is_absolute() else base / path


def password(values: Mapping[str, str], prefix: str, base: Path) -> str:
    key = prefix + "_PASSWORD_FILE"
    try:
        return read_secret(secret_path(values, key, base))
    except ConfigurationError as error:
        raise ConfigurationError(f"{key}: {error}") from None


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)
    timeout: int = 5
    query_timeout: int = 10


def settings(prefix: str, path: Path | None = None) -> DatabaseSettings:
    source = path if path is not None else environment_file()
    return from_values(prefix, read_values(source), source.parent)


class ConnectionParameters(TypedDict):
    host: str
    port: int
    database: str
    user: str
    timeout: int
    query_timeout: int


def connection_parameters(
    prefix: str, values: Mapping[str, str]
) -> ConnectionParameters:
    """Validate connection fields before credentials exist, without filesystem I/O."""
    if prefix not in {"PG", "MONGO"}:
        raise ConfigurationError("Невідомий тип бази даних.")
    # File-only credentials keep secrets out of Compose configuration and argv.
    if prefix + "_PASSWORD" in values:
        raise ConfigurationError(f"Використовуйте лише {prefix}_PASSWORD_FILE.")
    return {
        "host": host(values, prefix + "_HOST"),
        "port": number(
            values, prefix + "_PORT", 5433 if prefix == "PG" else 27018, 65535
        ),
        "database": identifier(values, prefix + "_DB"),
        "user": identifier(values, prefix + "_USER"),
        "timeout": number(values, "DB_TIMEOUT", 5, 30),
        "query_timeout": number(values, "DB_QUERY_TIMEOUT", 10, 300),
    }


def from_values(prefix: str, values: Mapping[str, str], base: Path) -> DatabaseSettings:
    """Validate an explicit configuration without consulting process environment."""
    return DatabaseSettings(
        **connection_parameters(prefix, values),
        password=password(values, prefix, base),
    )
