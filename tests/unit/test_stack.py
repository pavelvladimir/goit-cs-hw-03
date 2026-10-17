"""Prove test isolation and cleanup before running destructive database checks."""

import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from conf.logging import ToolError
from conf.settings import ROOT
from tests.integration.conftest import pytest_runtest_makereport
from tests.integration.stack import ComposeStack, running_stack


@pytest.fixture(autouse=True)
def no_network_in_unit_tests() -> Iterator[None]:
    with patch("scripts.stack.allocate_ports", return_value=("65430", "65431")):
        yield


@pytest.mark.parametrize(
    ("filename", "failure"),
    [
        ("pg_password_file", OSError("disk full")),
        ("pg_admin_password_file", OSError("disk full")),
        (".env", OSError("disk full")),
        ("pg_admin_password_file", KeyboardInterrupt()),
    ],
)
def test_partial_creation_rolls_back_only_owned_files(
    tmp_path: Path, filename: str, failure: BaseException
) -> None:
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("existing caller data")
    original_open = Path.open

    @contextmanager
    def interrupted_open(path: Path, mode: str = "r", **kwargs: Any) -> Iterator[Any]:
        with original_open(path, mode, **kwargs) as stream:
            if path.name == filename and mode == "x":
                writer = MagicMock(wraps=stream)

                def partial_write(content: str) -> int:
                    stream.write(content[:3])
                    stream.flush()
                    raise failure

                writer.write.side_effect = partial_write
                yield writer
            else:
                yield stream

    with patch.object(Path, "open", interrupted_open), pytest.raises(type(failure)):
        ComposeStack.create(tmp_path)
    assert list(tmp_path.iterdir()) == [unrelated]
    assert unrelated.read_text() == "existing caller data"


def test_failed_permissions_roll_back_completed_files(tmp_path: Path) -> None:
    original_chmod = Path.chmod

    def fail_env_permissions(path: Path, mode: int) -> None:
        if path.name == ".env":
            raise OSError("chmod failed")
        original_chmod(path, mode)

    with patch.object(Path, "chmod", fail_env_permissions):
        with pytest.raises(OSError, match="chmod failed"):
            ComposeStack.create(tmp_path)
    assert not list(tmp_path.iterdir())


def test_creation_preserves_existing_env_file(tmp_path: Path) -> None:
    existing = tmp_path / ".env"
    existing.write_text("caller configuration")
    with pytest.raises(FileExistsError):
        ComposeStack.create(tmp_path)
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_text() == "caller configuration"


def test_stack_ignores_development_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PG_HOST", "production.example")
    monkeypatch.setenv("PG_DB", "developer_database")
    monkeypatch.setenv("PG_PASSWORD_FILE", "/not/a/test/secret")
    monkeypatch.setenv("MONGODB_URI", "mongodb://secret@production.example/")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "developer-project")
    monkeypatch.setenv("COMPOSE_FILE", "some-other-compose.yaml")
    monkeypatch.setenv("HW03_ENV_FILE", "/not/a/test/env")
    before = (ROOT / ".env").read_bytes() if (ROOT / ".env").exists() else None
    stack = ComposeStack.create(tmp_path)
    assert stack.project.startswith("hw03-test-")
    assert "-p" in stack.command and stack.project in stack.command
    assert str(stack.env_file) in stack.command
    assert str(ROOT / "compose.test.yaml") in stack.command
    assert not set(stack.environment) & {
        "PG_HOST",
        "PG_DB",
        "PG_PASSWORD_FILE",
        "COMPOSE_PROJECT_NAME",
        "COMPOSE_FILE",
        "MONGODB_URI",
        "HW03_ENV_FILE",
    }
    with patch.object(stack, "port", return_value="65432"):
        for prefix in ("PG", "MONGO"):
            config = stack.database_settings(prefix)
            assert config.host == "127.0.0.1"
            assert config.port == 65432
            assert config.database != "developer_database"
            assert config.password in stack.passwords
        assert stack.app_environment()["HW03_ENV_FILE"] == str(stack.env_file)
    after = (ROOT / ".env").read_bytes() if (ROOT / ".env").exists() else None
    assert before == after
    assert all(secret not in repr(stack) for secret in stack.passwords)


@pytest.mark.parametrize("failure", ["up", "body", "none"])
def test_cleanup_on_success_startup_or_test_failure(
    tmp_path: Path, failure: str
) -> None:
    stack = ComposeStack.create(tmp_path)
    calls: list[tuple[str, ...]] = []

    def run(*args: str, timeout: int = 180) -> str:
        calls.append(args)
        if args[0] == "up" and failure == "up":
            raise RuntimeError("intentional startup failure")
        return "sanitized logs"

    with patch.object(stack, "run", side_effect=run):
        if failure == "none":
            with running_stack(stack):
                pass
        else:
            with pytest.raises(RuntimeError, match="intentional"):
                with running_stack(stack):
                    raise RuntimeError("intentional test failure")
    assert calls[-1] == ("down", "--volumes", "--remove-orphans")
    assert not stack.env_file.exists()
    assert not (tmp_path / "secrets").exists()


def test_tool_output_is_sanitized(tmp_path: Path) -> None:
    stack = ComposeStack.create(tmp_path)
    with patch("scripts.stack.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 1, "", stack.passwords[0])
        with pytest.raises(ToolError) as error:
            stack.run("up")
        assert stack.passwords[0] not in str(error.value)
        run.side_effect = subprocess.TimeoutExpired("docker", 1)
        with pytest.raises(RuntimeError, match="timed out"):
            stack.run("up")


def test_dynamic_port_must_be_loopback(tmp_path: Path) -> None:
    stack = ComposeStack.create(tmp_path)
    with patch.object(stack, "run", return_value="127.0.0.1:65432\n"):
        assert stack.port("postgres", 5432) == "65432"
    with patch.object(stack, "run", return_value="0.0.0.0:5432\n"):
        with pytest.raises(RuntimeError, match="loopback"):
            stack.port("postgres", 5432)


@pytest.mark.parametrize("failed", [True, False])
def test_pytest_failures_receive_diagnostic_logs(tmp_path: Path, failed: bool) -> None:
    stack = ComposeStack.create(tmp_path)
    item = MagicMock(spec=pytest.Item)
    item.funcargs = {"stack": stack}
    report = MagicMock(spec=pytest.TestReport)
    report.failed = failed
    report.sections = []
    hook = pytest_runtest_makereport(item)
    next(hook)
    with patch.object(stack, "run", return_value="sanitized diagnostics") as run:
        with pytest.raises(StopIteration) as done:
            hook.send(report)
        assert done.value.value is report
        assert run.call_count == int(failed)
    assert bool(report.sections) == failed


def test_failed_cleanup_preserves_config_for_recovery(tmp_path: Path) -> None:
    stack = ComposeStack.create(tmp_path)
    with patch.object(stack, "run", side_effect=["", RuntimeError("cleanup failed")]):
        with pytest.raises(RuntimeError, match="cleanup failed"):
            with running_stack(stack):
                pass
    assert stack.env_file.exists()
    assert (tmp_path / "secrets").is_dir()


@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.parametrize("ensure_ascii", [False, True])
def test_compose_json_diagnostics_redact_escaped_secrets(
    tmp_path: Path, returncode: int, ensure_ascii: bool
) -> None:
    stack = ComposeStack.create(tmp_path)
    diagnostic = json.dumps(
        {"passwords": list(stack.passwords)}, ensure_ascii=ensure_ascii
    )
    with patch(
        "scripts.stack.subprocess.run",
        return_value=subprocess.CompletedProcess([], returncode, diagnostic, ""),
    ):
        if returncode:
            with pytest.raises(RuntimeError) as error:
                stack.run("logs")
            cleaned = str(error.value).split("\n", 1)[1]
        else:
            cleaned = stack.run("logs")
    assert json.loads(cleaned) == {"passwords": ["[REDACTED]"] * 4}


@pytest.mark.parametrize(
    ("service", "prefix", "other"),
    [
        ("postgres", "PG", "MONGO"),
        ("mongodb", "MONGO", "PG"),
    ],
)
def test_single_database_stack_never_discovers_or_starts_other_service(
    tmp_path: Path, service: str, prefix: str, other: str
) -> None:
    stack = ComposeStack.create(tmp_path, services=(service,))
    with patch.object(stack, "run", return_value="") as run:
        stack.start()
        assert run.call_args.args[-1:] == (service,)
        assert not set(run.call_args.args) & ({"postgres", "mongodb"} - {service})
    with patch.object(stack, "port", return_value="65432") as port:
        environment = stack.app_environment()
        assert environment[prefix + "_PORT"] == "65432"
        assert other + "_PORT" not in environment
        assert port.call_count == 1
        with pytest.raises(ValueError, match="not selected"):
            stack.database_settings(other)
        assert port.call_count == 1
    stack.cleanup_files()


@pytest.mark.parametrize("services", [(), ("unknown",)])
def test_invalid_service_selection_creates_no_credentials(
    tmp_path: Path, services: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError):
        ComposeStack.create(tmp_path, services=services)
    assert not list(tmp_path.iterdir())


def test_real_external_process_timeout_reaps_child(tmp_path: Path) -> None:
    import os
    import sys

    pid = tmp_path / "pid"
    stack = ComposeStack.create(tmp_path)
    stack.command = [
        sys.executable,
        "-c",
        "import os,time; from pathlib import Path; "
        "Path(os.environ['CHILD_PID']).write_text(str(os.getpid())); time.sleep(60)",
    ]
    stack.environment["CHILD_PID"] = str(pid)
    try:
        with pytest.raises(RuntimeError, match="timed out"):
            stack.run("probe", timeout=1)
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid.read_text()), 0)
    finally:
        stack.cleanup_files()
