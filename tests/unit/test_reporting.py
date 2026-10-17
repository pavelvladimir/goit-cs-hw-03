"""Exercise actual pytest terminal/JUnit reporters, including fixture failures."""

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, quote_plus

import pytest

from conf.settings import ROOT

CHILD = """import json, logging, os, sys
from types import SimpleNamespace
from urllib.parse import quote, quote_plus
import pytest
from tests.reporting import register_secrets
secret = os.environ["REPORT_SECRET"]
register_secrets([secret])
class BrokenDisplay:
    def __str__(self):
        raise ValueError(secret)
variants = [secret, quote(secret, safe=""), quote_plus(secret),
            json.dumps(secret)[1:-1], repr(secret)[1:-1], ascii(secret)[1:-1],
            secret.replace("\\\\", "\\\\\\\\").replace("'", "\\\\'")]
def fail():
    for value in variants:
        print(value)
        print(value, file=sys.stderr)
        logging.warning(value)
    raise RuntimeError("useful cause: " + " | ".join(variants))
@pytest.fixture
def resource(request):
    request.node.user_properties.extend([
        ("credential", {"nested": variants}),
        ("binary", secret.encode()),
        ("mutable_binary", bytearray(secret.encode())),
        ("object", SimpleNamespace(password=secret)),
        ("nested", {"values": [SimpleNamespace(password=secret.encode()),
                              (bytearray(secret.encode()), {secret})]}),
        ("broken_display", BrokenDisplay()),
        ("number", 42),
        ("flag", True),
        ("empty", None),
        ("useful", "diagnostic context"),
    ])
    if os.environ["REPORT_PHASE"] == "setup":
        fail()
    if os.environ["REPORT_PHASE"] == "skipped":
        pytest.skip("controlled skip " + secret)
    yield
    if os.environ["REPORT_PHASE"] == "teardown":
        fail()
def test_diagnostic(resource):
    if os.environ["REPORT_PHASE"] == "call":
        fail()
"""


@pytest.mark.parametrize("phase", ["setup", "call", "teardown", "passed", "skipped"])
def test_entire_child_pytest_report_is_redacted(tmp_path: Path, phase: str) -> None:
    secret = "report-marker-@:/?#%$&'\"\\ space Ж"
    child = tmp_path / "test_redaction_child.py"
    child.write_text(CHILD, encoding="utf-8")
    report = tmp_path / "junit.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(ROOT / "pyproject.toml"),
            "-p",
            "tests.reporting",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-o",
            "junit_logging=all",
            "--showlocals",
            "--junitxml",
            str(report),
            str(child),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ | {"REPORT_SECRET": secret, "REPORT_PHASE": phase},
    )
    failed = phase in {"setup", "call", "teardown"}
    assert result.returncode == int(failed), result.stdout + result.stderr
    properties = {
        node.attrib["name"]: node.attrib["value"]
        for node in ET.parse(report).iter("property")
    }
    assert properties["useful"] == "diagnostic context"
    assert properties["number"] == "42"
    assert properties["flag"] == "True"
    assert properties["empty"] == "None"
    assert properties["broken_display"] == "[UNPRINTABLE]"
    if phase == "skipped":
        skipped = ET.parse(report).find(".//skipped")
        assert skipped is not None
        assert skipped.attrib["message"] == "controlled skip [REDACTED]"
    for name in ("credential", "binary", "mutable_binary", "object", "nested"):
        assert "[REDACTED]" in properties[name]
    for output in (result.stdout + result.stderr, report.read_text()):
        if failed:
            assert "useful cause" in output
            assert "RuntimeError" in output
            assert "test_redaction_child.py" in output
            assert "[REDACTED]" in output
        assert "report-marker-" not in output
        for variant in (
            secret,
            quote(secret, safe=""),
            quote_plus(secret),
            json.dumps(secret)[1:-1],
            repr(secret)[1:-1],
        ):
            assert variant not in output
