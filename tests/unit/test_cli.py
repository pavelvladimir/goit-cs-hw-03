"""CLI success and safe failure behavior."""

import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import psycopg
import pytest
from pymongo.collection import Collection
from pymongo.errors import ConnectionFailure, OperationFailure, PyMongoError

import create_tables
import main as cats
import seed
from conf.settings import (
    ROOT,
)


class CommandLineTests(unittest.TestCase):
    def test_postgresql_errors_return_nonzero(self) -> None:
        for module in (create_tables, seed):
            errors = io.StringIO()
            with (
                self.subTest(module=module.__name__),
                patch.object(
                    module, "connect", side_effect=psycopg.OperationalError("offline")
                ),
                redirect_stderr(errors),
            ):
                self.assertEqual(module.main(), 1)
            self.assertNotIn("offline", errors.getvalue())
            self.assertIn("Перевірте", errors.getvalue())

    def test_postgresql_success_closes_connection(self) -> None:
        for module, operation in (
            (create_tables, "create_tables"),
            (seed, "seed_database"),
        ):
            with (
                self.subTest(module=module.__name__),
                patch.object(module, "connect") as connect,
                patch.object(module, operation) as action,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(module.main(), 0)
                action.assert_called_once_with(
                    connect.return_value.__enter__.return_value
                )
                connect.return_value.__exit__.assert_called_once_with(None, None, None)

    def test_mongodb_startup_failure_returns_nonzero(self) -> None:
        errors = io.StringIO()
        with (
            patch("sys.argv", ["main.py"]),
            patch.object(cats, "mongo_client") as client,
            patch.object(cats, "settings"),
            redirect_stderr(errors),
        ):
            client.side_effect = ConnectionFailure("offline")
            self.assertEqual(cats.main(), 1)
        self.assertNotIn("offline", errors.getvalue())
        self.assertIn("Перевірте", errors.getvalue())

    def test_mongodb_startup_interrupt_exits_cleanly(self) -> None:
        with (
            patch("sys.argv", ["main.py"]),
            patch.object(cats, "mongo_client") as client,
            patch.object(cats, "settings"),
            redirect_stdout(io.StringIO()),
        ):
            client.side_effect = KeyboardInterrupt
            self.assertEqual(cats.main(), 0)


@pytest.mark.parametrize(("choice", "value"), [("4", "4"), ("5", "лагідний")])
@pytest.mark.parametrize(
    "error",
    [
        ConnectionFailure("private-driver-details"),
        OperationFailure("private-driver-details", code=13),
    ],
)
def test_mongodb_update_failure_does_not_report_success_and_menu_continues(
    choice: str, value: str, error: PyMongoError, capsys: pytest.CaptureFixture[str]
) -> None:
    collection = Mock(spec=Collection)
    collection.update_one.side_effect = error
    collection.find_one.return_value = {"name": "barsik", "age": 3, "features": []}
    with patch(
        "builtins.input", side_effect=[choice, "barsik", value, "2", "barsik", "0"]
    ):
        cats.run_menu(cast(Collection[cats.CatDocument], collection))
    output = capsys.readouterr()
    assert "Помилка MongoDB" in output.err
    assert "private-driver-details" not in output.err
    assert "private-driver-details" not in output.out
    assert "збережено" not in output.out
    assert "Змін не внесено" not in output.out
    assert "Кота не знайдено" not in output.out
    assert '"name": "barsik"' in output.out
    collection.update_one.assert_called_once()
    collection.find_one.assert_called_once_with({"name": "barsik"})


@pytest.mark.parametrize("script", ["create_tables.py", "seed.py", "main.py"])
def test_real_cli_configuration_error(script: str, tmp_path: Path) -> None:
    env = dict(os.environ) | {"PG_DB": "must-not-leak;", "MONGO_DB": "must-not-leak;"}
    result = subprocess.run(
        [sys.executable, str(ROOT / script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr
    assert "must-not-leak" not in result.stderr
    assert "Traceback" not in result.stderr
