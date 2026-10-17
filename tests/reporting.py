"""Redact registered ephemeral credentials before any pytest reporter sees them."""

from collections.abc import Generator, Iterable

import pytest

from conf.logging import redact

_secrets: set[str] = set()


def register_secrets(values: Iterable[str]) -> None:
    for value in values:
        if value:
            # Containers/objects can render UTF-8 bytes using ASCII escape sequences.
            _secrets.update((value, repr(value.encode("utf-8"))[2:-1]))


def clean(value: object) -> str:
    # JUnit ultimately calls str(value). Freeze and sanitize that representation now
    # so a reporter cannot later expose an unhandled object, bytes or nested value.
    try:
        rendered = str(value)
    except Exception:
        return "[UNPRINTABLE]"
    return redact(rendered, _secrets)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport() -> Generator[
    None, pytest.TestReport, pytest.TestReport
]:
    # Outermost wrapper: include diagnostics attached by other makereport hooks.
    report = yield
    if isinstance(report.longrepr, tuple):
        # Skipped reports have a tuple contract required by the JUnit reporter.
        skip_path, skip_line, skip_reason = report.longrepr
        report.longrepr = (
            redact(skip_path, _secrets),
            skip_line,
            redact(skip_reason, _secrets),
        )
    elif report.longrepr is not None:
        report.longrepr = redact(str(report.longrepr), _secrets)
    report.sections = [
        (redact(k, _secrets), redact(v, _secrets)) for k, v in report.sections
    ]
    report.user_properties = [
        (redact(key, _secrets), clean(value)) for key, value in report.user_properties
    ]
    report.nodeid = redact(report.nodeid, _secrets)
    path, line, name = report.location
    report.location = (redact(path, _secrets), line, redact(name, _secrets))
    return report
