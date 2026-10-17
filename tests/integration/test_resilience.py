"""Exercise real database locks, process interruption and server outages."""

import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

from conf.db import connect, mongo_client
from conf.settings import ROOT
from create_tables import create_tables
from tests.integration.stack import ComposeStack


def eventually(condition: Callable[[], bool], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("Condition did not become true before the deadline.")


@pytest.mark.postgresql
def test_statement_and_lock_timeouts(stack: ComposeStack) -> None:
    config = replace(stack.database_settings("PG"), query_timeout=1)
    with connect(config) as connection:
        create_tables(connection)
    with connect(config) as connection:
        assert connection.execute("SHOW statement_timeout").fetchone() == ("1s",)
        assert connection.execute("SHOW lock_timeout").fetchone() == ("1s",)
        start = time.monotonic()
        with pytest.raises(psycopg.errors.QueryCanceled):
            connection.execute("SELECT pg_sleep(10)")
        assert time.monotonic() - start < 4
        connection.rollback()
        assert connection.execute("SELECT 1").fetchone() == (1,)
    with connect(config) as locker, connect(config) as blocked:
        locker.execute("LOCK TABLE tasks IN ACCESS EXCLUSIVE MODE")
        blocked.execute("SET statement_timeout = '5s'")
        start = time.monotonic()
        with pytest.raises(psycopg.errors.LockNotAvailable):
            blocked.execute("SELECT * FROM tasks")
        assert time.monotonic() - start < 4
        blocked.rollback()


@pytest.mark.postgresql
def test_sigint_during_seed_rolls_back_every_table(stack: ComposeStack) -> None:
    config = stack.database_settings("PG")
    with connect(config) as connection:
        create_tables(connection)
        connection.execute("TRUNCATE tasks, users, status RESTART IDENTITY CASCADE")
    environment = stack.app_environment() | {"DB_QUERY_TIMEOUT": "30"}
    with connect(config) as locker, connect(config) as observer:
        locker.execute("LOCK TABLE tasks IN ACCESS EXCLUSIVE MODE")
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "seed.py")],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:

            def seed_is_blocked() -> bool:
                # A fresh statistics snapshot on every poll sees the live child session.
                observer.rollback()
                row = observer.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE usename = %s AND pid <> pg_backend_pid() "
                    "AND wait_event_type = 'Lock' "
                    "AND query LIKE '%%INSERT INTO tasks%%'",
                    (config.user,),
                ).fetchone()
                return row == (1,)

            eventually(seed_is_blocked)
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 130
            assert stdout == ""
            assert "[INTERRUPTED]" in stderr
            assert "Traceback" not in stderr
            assert all(password not in stderr for password in stack.passwords)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
            locker.rollback()
    with connect(config) as connection:
        assert connection.execute(
            "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM status), "
            "(SELECT count(*) FROM tasks)"
        ).fetchone() == (0, 0, 0)


@pytest.mark.mongodb
def test_menu_recovers_after_real_server_outage(
    stack: ComposeStack, tmp_path: Path
) -> None:
    config = stack.database_settings("MONGO")
    marker = "RecoveryCat"
    with mongo_client(config) as client:
        client[config.database].cats.replace_one(
            {"name": marker}, {"name": marker, "age": 3, "features": []}, upsert=True
        )
    stdout_path, stderr_path = tmp_path / "stdout", tmp_path / "stderr"
    environment = stack.app_environment() | {"DB_TIMEOUT": "1", "DB_QUERY_TIMEOUT": "3"}
    with stdout_path.open("w") as output, stderr_path.open("w") as errors:
        process = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "main.py")],
            env=environment,
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=errors,
            text=True,
        )
        assert process.stdin is not None
        try:
            eventually(lambda: "Показати всіх котів" in stdout_path.read_text())
            process.stdin.write("1\n")
            process.stdin.flush()
            eventually(lambda: marker in stdout_path.read_text())
            previous = stdout_path.read_text().count(marker)
            stack.run("stop", "mongodb")
            start = time.monotonic()
            process.stdin.write("1\n")
            process.stdin.flush()
            eventually(
                lambda: (
                    "[TIMEOUT]" in stderr_path.read_text()
                    or "[CONNECTION]" in stderr_path.read_text()
                ),
                timeout=6,
            )
            assert time.monotonic() - start < 6
            assert process.poll() is None
            stack.start()
            process.stdin.write("1\n")
            process.stdin.flush()
            eventually(lambda: stdout_path.read_text().count(marker) > previous)
            process.stdin.write("0\n")
            process.stdin.flush()
            process.wait(timeout=5)
            assert process.returncode == 0
            diagnostic = stderr_path.read_text()
            assert "Traceback" not in diagnostic
            assert all(password not in diagnostic for password in stack.passwords)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdin.close()
            stack.start()
