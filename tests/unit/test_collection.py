"""Actual pytest runs must classify tests independently of checkout location."""

import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from conf.settings import ROOT

UNIT = """def TEST_NAME(request):
    assert request.node.get_closest_marker('integration') is None
"""
INTEGRATION = """def test_database(request):
    assert request.config.getoption('--integration')
    assert request.node.get_closest_marker('integration') is not None
"""


@pytest.mark.parametrize("parent", ["workspace", "integration"])
@pytest.mark.parametrize(
    "arguments, expected, skipped",
    [
        ([], {"test_unit", "test_nested_unit"}, set()),
        (["--integration"], {"test_unit", "test_nested_unit", "test_database"}, set()),
        (
            ["--integration", "-m", "not integration"],
            {"test_unit", "test_nested_unit"},
            set(),
        ),
        (["tests/integration/test_database.py"], set(), {"test_database"}),
    ],
)
def test_collection_in_relocated_checkout(
    tmp_path: Path,
    parent: str,
    arguments: list[str],
    expected: set[str],
    skipped: set[str],
) -> None:
    project = tmp_path / parent / "project"
    tests = project / "tests"
    (tests / "unit" / "integration").mkdir(parents=True)
    (tests / "integration").mkdir()
    for relative in (
        "pyproject.toml",
        "tests/__init__.py",
        "tests/conftest.py",
        "tests/reporting.py",
    ):
        shutil.copyfile(ROOT / relative, project / relative)
    shutil.copytree(
        ROOT / "conf", project / "conf", ignore=shutil.ignore_patterns("__pycache__")
    )
    (tests / "unit" / "test_unit.py").write_text(UNIT.replace("TEST_NAME", "test_unit"))
    (tests / "unit" / "integration" / "test_nested.py").write_text(
        UNIT.replace("TEST_NAME", "test_nested_unit")
    )
    (tests / "integration" / "test_database.py").write_text(INTEGRATION)
    report = project / "junit.xml"
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")
    }
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--junitxml",
            str(report),
            *arguments,
        ],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    cases = list(ET.parse(report).iter("testcase"))
    assert {
        case.attrib["name"] for case in cases if case.find("skipped") is None
    } == expected
    assert {
        case.attrib["name"] for case in cases if case.find("skipped") is not None
    } == skipped
