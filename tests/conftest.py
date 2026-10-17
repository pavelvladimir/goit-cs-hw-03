"""Keep integration tests explicitly opt-in, including direct collection."""

from pathlib import Path

import pytest

pytest_plugins = ["tests.reporting"]
INTEGRATION_DIRECTORY = Path(__file__).resolve().parent / "integration"


def is_integration_path(path: Path) -> bool:
    return path.resolve().is_relative_to(INTEGRATION_DIRECTORY)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration", action="store_true", help="Run isolated Percona tests"
    )


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    if is_integration_path(collection_path) and not config.getoption("--integration"):
        return True
    return None


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    for item in items:
        if is_integration_path(item.path):
            item.add_marker(pytest.mark.integration)
            if not config.getoption("--integration"):
                item.add_marker(
                    pytest.mark.skip(reason="Pass --integration explicitly")
                )
