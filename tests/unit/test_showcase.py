"""Fail before Docker for invalid input; never publish a failed measurement."""

import json
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from conf.logging import ToolError
from conf.settings import ROOT
from scripts import benchmark, demo, queries
from scripts.stack import isolated_stack


def test_query_sections_preserve_sql_and_reject_missing_number(tmp_path: Path) -> None:
    source = (ROOT / "queries.sql").read_text()
    source = source.replace("'Вивчити PostgreSQL'", "'Вивчити; PostgreSQL'")
    path = tmp_path / "queries.sql"
    path.write_text(source)
    with patch.object(queries, "ROOT", tmp_path):
        sections = queries.homework_queries()
        assert len(sections) == 14
        assert "'Вивчити; PostgreSQL'" in sections[4].sql
        assert "COMMIT" not in sections[-1].sql
        assert sections[0].sql == "SELECT * FROM tasks WHERE user_id = 1 ORDER BY id;"
        path.write_text(source.replace("-- 8.", "-- 9."))
        with pytest.raises(ValueError, match="1–14"):
            queries.homework_queries()


@pytest.mark.parametrize("failure", [None, RuntimeError("body"), KeyboardInterrupt()])
def test_disposable_directory_cleanup_on_success_error_and_interrupt(
    tmp_path: Path, failure: BaseException | None
) -> None:
    directory = tmp_path / "run"
    directory.mkdir()
    with (
        patch("scripts.stack.tempfile.mkdtemp", return_value=str(directory)),
        patch("scripts.stack.allocate_ports", return_value=("65430", "65431")),
        patch("scripts.stack.ComposeStack.run", return_value="") as run,
    ):

        def exercise() -> None:
            with isolated_stack("demo") as stack:
                assert stack.project.startswith("hw03-demo-")
                assert stack.env_file.exists()
                if failure:
                    raise failure

        if failure:
            with pytest.raises(type(failure)):
                exercise()
        else:
            exercise()
        assert run.call_args.args == ("down", "--volumes", "--remove-orphans")
    assert not directory.exists()


@pytest.mark.parametrize("failure", [OSError("disk full"), KeyboardInterrupt()])
def test_partial_factory_failure_removes_disposable_directory(
    tmp_path: Path, failure: BaseException
) -> None:
    directory = tmp_path / "run"
    directory.mkdir()
    with (
        patch("scripts.stack.tempfile.mkdtemp", return_value=str(directory)),
        patch("scripts.stack.allocate_ports", return_value=("65430", "65431")),
        patch(
            "scripts.stack.ComposeStack.secret_observer", side_effect=[None, failure]
        ),
        patch("scripts.stack.ComposeStack.run") as docker,
    ):
        with pytest.raises(type(failure)):
            with isolated_stack("demo"):
                pytest.fail("Factory failure must precede Docker startup")
        docker.assert_not_called()
    assert not directory.exists()


def test_failed_cleanup_keeps_cli_recovery_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "run"
    directory.mkdir()
    with (
        patch("scripts.stack.tempfile.mkdtemp", return_value=str(directory)),
        patch("scripts.stack.allocate_ports", return_value=("65430", "65431")),
        patch("scripts.stack.ComposeStack.run", side_effect=["", RuntimeError("down")]),
        pytest.raises(RuntimeError, match="down"),
        isolated_stack("benchmark", ("postgres",)),
    ):
        pass
    assert (directory / ".env").exists()
    assert len(list((directory / "secrets").iterdir())) == 4
    assert str(directory / ".env") in capsys.readouterr().err


@pytest.mark.parametrize(
    "args", [["--rows", "29"], ["--rows", "1000001"], ["--repeats", "2"]]
)
def test_invalid_benchmark_arguments_do_not_start_docker(args: list[str]) -> None:
    with (
        patch("sys.argv", ["benchmark", *args]),
        patch.object(benchmark, "isolated_stack") as stack,
        pytest.raises(SystemExit) as error,
    ):
        benchmark.main()
    assert error.value.code == 2
    stack.assert_not_called()


@pytest.mark.parametrize("phase", ["success", "measurement", "cleanup", "interrupt"])
def test_benchmark_publishes_only_after_measurement_and_cleanup(
    tmp_path: Path, phase: str, capsys: pytest.CaptureFixture[str]
) -> None:
    completed = []
    stack = MagicMock()
    stack.run.return_value = "example-image"
    report: dict[str, Any] = {"datasets": []}

    @contextmanager
    def lifecycle(*args: object) -> Iterator[MagicMock]:
        try:
            yield stack
        finally:
            assert not list(tmp_path.iterdir())
            if phase == "cleanup":
                raise RuntimeError("private-driver-detail")
            completed.append("cleanup")

    failure = (
        RuntimeError("private-driver-detail")
        if phase == "measurement"
        else KeyboardInterrupt()
        if phase == "interrupt"
        else None
    )
    with (
        patch("sys.argv", ["benchmark", "--rows", "30", "--output", str(tmp_path)]),
        patch.object(benchmark, "isolated_stack", lifecycle),
        patch.object(benchmark, "connect", return_value=nullcontext(MagicMock())),
        patch.object(benchmark, "benchmark", return_value=report, side_effect=failure),
    ):
        status = benchmark.main()
    assert status == (0 if phase == "success" else 130 if phase == "interrupt" else 1)
    if phase == "success":
        result = next(tmp_path.glob("*/results.json"))
        assert json.loads(result.read_text())["datasets"] == []
        assert result.with_name("summary.md").is_file()
        assert completed == ["cleanup"]
    else:
        assert not list(tmp_path.iterdir())
    assert "private-driver-detail" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "failure", [None, RuntimeError("private-driver-detail"), KeyboardInterrupt()]
)
def test_demo_exit_status_and_safe_errors(
    failure: BaseException | None, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        patch("sys.argv", ["demo"]),
        patch.object(demo, "isolated_stack", return_value=nullcontext(MagicMock())),
        patch.object(demo, "connect", return_value=nullcontext(MagicMock())),
        patch.object(demo, "mongo_client", return_value=MagicMock()),
        patch.object(demo, "postgres_demo", side_effect=failure),
        patch.object(demo, "mongo_demo") as crud,
    ):
        status = demo.main()
    assert status == (
        0 if failure is None else 130 if isinstance(failure, KeyboardInterrupt) else 1
    )
    assert crud.call_count == int(failure is None)
    assert "private-driver-detail" not in capsys.readouterr().err


@pytest.mark.parametrize("module", [demo, benchmark])
def test_cli_preserves_sanitized_compose_failure(
    module: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    failure = ToolError(
        "Compose up failed: ports are not available; secret-marker", ["secret-marker"]
    )
    with (
        patch("sys.argv", [module.__name__]),
        patch.object(module, "isolated_stack", side_effect=failure),
    ):
        assert module.main() == 1
    diagnostic = capsys.readouterr().err
    assert "[OPERATION]" in diagnostic
    assert "ports are not available" in diagnostic
    assert "secret-marker" not in diagnostic
