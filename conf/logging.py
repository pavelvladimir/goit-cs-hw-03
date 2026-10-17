"""Safe CLI diagnostics and redaction of external tool output."""

import json
import re
import sys
from collections.abc import Iterable
from urllib.parse import quote, quote_plus

import psycopg
from pymongo import errors as mongo_errors

from conf.settings import ConfigurationError

MESSAGES = {
    "AUTH": "Перевірте користувача, файл пароля та ротацію облікових даних.",
    "PERMISSION": "Перевірте права прикладної ролі в обраній базі даних.",
    "TIMEOUT": (
        "Перевірте доступність сервера, навантаження й блокування; "
        "ліміт часу вичерпано."
    ),
    "CONNECTION": "Перевірте стан контейнера, адресу, порт і облікові дані.",
    "SCHEMA": "Перевірте схему; спочатку запустіть create_tables.py.",
    "DATABASE": "Перевірте операцію та стан бази даних.",
}


class ToolError(RuntimeError):
    """Operational diagnostics explicitly sanitized before reaching the CLI."""

    def __init__(self, message: str, secrets: Iterable[str] = ()) -> None:
        super().__init__(redact(message, secrets))


def error_category(error: Exception) -> str:
    """Use structured codes only; driver messages can contain credentials or SQL."""
    if isinstance(error, psycopg.Error):
        state = error.sqlstate or ""
        if state.startswith("28"):
            return "AUTH"
        if state == "42501":
            return "PERMISSION"
        if state in {"57014", "55P03"} or isinstance(
            error, psycopg.errors.ConnectionTimeout
        ):
            return "TIMEOUT"
        if state in {"42P01", "42703", "3F000"}:
            return "SCHEMA"
        if isinstance(error, psycopg.OperationalError | psycopg.InterfaceError):
            return "CONNECTION"
    if isinstance(error, mongo_errors.OperationFailure):
        if error.code == 18:
            return "AUTH"
        if error.code == 13:
            return "PERMISSION"
    if isinstance(error, mongo_errors.PyMongoError):
        if error.timeout:
            return "TIMEOUT"
        if isinstance(error, mongo_errors.ConnectionFailure):
            return "CONNECTION"
    return "DATABASE"


def report_error(context: str, error: Exception) -> None:
    if isinstance(error, ConfigurationError):
        category, detail = "CONFIG", str(error)
    elif isinstance(error, ToolError):
        category, detail = "OPERATION", str(error)
    else:
        category = error_category(error)
        detail = MESSAGES[category]
    print(f"{context} [{category}]: {detail}", file=sys.stderr)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    variants = {
        encoded
        for secret in secrets
        if secret
        for encoded in (
            secret,
            quote(secret, safe=""),
            quote_plus(secret),
            json.dumps(secret, ensure_ascii=False)[1:-1],
            json.dumps(secret, ensure_ascii=True)[1:-1],
            repr(secret)[1:-1],
            ascii(secret)[1:-1],
            secret.replace("\\", "\\\\").replace("'", "\\'"),
        )
    }
    # Pytest can render a driver message through another repr/JSON layer.
    variants |= {
        escaped
        for value in tuple(variants)
        for escaped in (repr(value)[1:-1], json.dumps(value)[1:-1])
    }
    # Longest first: a raw substring must not break its escaped representation.
    for encoded in sorted(variants, key=len, reverse=True):
        text = text.replace(encoded, "[REDACTED]")
    text = re.sub(
        r"(?i)\b(postgres(?:ql)?|mongodb(?:\+srv)?)://[^\s]+",
        r"\1://[REDACTED]",
        text,
    )
    return text
