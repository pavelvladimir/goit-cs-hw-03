"""Start only the database requested by each test; always isolate credentials."""

import os
from collections.abc import Generator, Iterator
from typing import cast

import pytest

from tests.integration.stack import ComposeStack, running_stack


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item,
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if report.failed:
        instance = getattr(item, "funcargs", {}).get("stack")
        if isinstance(instance, ComposeStack):
            try:
                logs = instance.run("logs", "--no-color", "--tail", "50")
            except RuntimeError:
                logs = "Container logs unavailable."
            report.sections.append(("Percona diagnostics (redacted)", logs))
    return report


@pytest.fixture(scope="session")
def postgres_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ComposeStack]:
    instance = ComposeStack.create(
        tmp_path_factory.mktemp("percona-pg"), services=("postgres",)
    )
    with running_stack(instance):
        yield instance


@pytest.fixture(scope="session")
def mongo_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ComposeStack]:
    instance = ComposeStack.create(
        tmp_path_factory.mktemp("percona-mongo"), services=("mongodb",)
    )
    with running_stack(instance):
        yield instance


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if (
            key.startswith(("PG", "MONGO", "DB_", "COMPOSE_", "HW03_"))
            or key == "DATABASE_URL"
        ):
            monkeypatch.delenv(key)


@pytest.fixture
def stack(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> ComposeStack:
    pg = request.node.get_closest_marker("postgresql") is not None
    mongo = request.node.get_closest_marker("mongodb") is not None
    if pg == mongo:
        raise ValueError("A database test must select exactly one database marker.")
    instance = cast(
        ComposeStack, request.getfixturevalue("postgres_stack" if pg else "mongo_stack")
    )
    for key, value in instance.app_environment().items():
        monkeypatch.setenv(key, value)
    return instance
