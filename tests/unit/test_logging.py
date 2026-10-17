"""Safe CLI errors and redaction of external diagnostics."""

import json
from urllib.parse import quote, quote_plus

import psycopg
import pytest
from pymongo import errors as mongo_errors

from conf.logging import ToolError, redact, report_error


def test_redaction_and_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    secret = "p@ss /?#&"
    message = f"{secret} {quote(secret, safe='')} mongodb://user:pw@host/db"
    cleaned = redact(message, [secret, ""])
    assert secret not in cleaned
    assert quote(secret, safe="") not in cleaned
    assert "user:pw" not in cleaned
    report_error("Database", ValueError(message))
    output = capsys.readouterr()
    assert output.out == ""
    assert secret not in output.err


@pytest.mark.parametrize(
    "secret", ['DEMO"\\heslo', "žluťoučký Ж 密碼🔑", 'x /@:#?$&\\" y']
)
@pytest.mark.parametrize("ensure_ascii", [False, True])
def test_json_escaped_secrets_remain_valid_json(
    secret: str, ensure_ascii: bool
) -> None:
    message = json.dumps(
        {"nested": [{"password": secret}], "safe": 42}, ensure_ascii=ensure_ascii
    )
    cleaned = redact(message, [secret, ""])
    assert json.loads(cleaned) == {"nested": [{"password": "[REDACTED]"}], "safe": 42}
    for variant in (secret, quote(secret, safe=""), quote_plus(secret)):
        assert redact(variant, [secret]) == "[REDACTED]"


def test_longer_secrets_are_redacted_before_shared_prefixes() -> None:
    assert (
        redact("prefix-long prefix", ["prefix", "prefix-long"])
        == "[REDACTED] [REDACTED]"
    )


def test_only_explicit_safe_tool_errors_expose_operational_details(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "private /@secret Ж"
    error = ToolError(
        f"Compose up failed: ports are not available; {quote(secret, safe='')}",
        [secret],
    )
    report_error("Demo", error)
    diagnostic = capsys.readouterr().err
    assert "[OPERATION]" in diagnostic
    assert "ports are not available" in diagnostic
    assert "[REDACTED]" in diagnostic
    assert "private" not in diagnostic
    report_error("Demo", RuntimeError("untrusted private detail"))
    diagnostic = capsys.readouterr().err
    assert "[DATABASE]" in diagnostic
    assert "private" not in diagnostic


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (psycopg.errors.InvalidPassword("secret SQL"), "AUTH"),
        (psycopg.errors.InsufficientPrivilege("secret SQL"), "PERMISSION"),
        (psycopg.errors.QueryCanceled("secret SQL"), "TIMEOUT"),
        (psycopg.errors.ConnectionTimeout("secret SQL"), "TIMEOUT"),
        (psycopg.errors.LockNotAvailable("secret SQL"), "TIMEOUT"),
        (psycopg.errors.UndefinedTable("secret SQL"), "SCHEMA"),
        (psycopg.OperationalError("secret SQL"), "CONNECTION"),
        (psycopg.IntegrityError("secret SQL"), "DATABASE"),
        (mongo_errors.OperationFailure("secret URI", code=18), "AUTH"),
        (mongo_errors.OperationFailure("secret URI", code=13), "PERMISSION"),
        (mongo_errors.ExecutionTimeout("secret URI", code=50), "TIMEOUT"),
        (mongo_errors.NetworkTimeout("secret URI"), "TIMEOUT"),
        (mongo_errors.ConnectionFailure("secret URI"), "CONNECTION"),
        (mongo_errors.OperationFailure("secret URI", code=999), "DATABASE"),
        (ValueError("secret arbitrary input"), "DATABASE"),
    ],
)
def test_safe_actionable_driver_errors(
    error: Exception, category: str, capsys: pytest.CaptureFixture[str]
) -> None:
    report_error("Operation failed", error)
    diagnostic = capsys.readouterr().err
    assert f"[{category}]" in diagnostic
    assert "Перевірте" in diagnostic
    assert "secret" not in diagnostic
