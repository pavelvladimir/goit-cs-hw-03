"""Container deadlines and cancellation must stop native work, not just Docker CLI."""

import signal
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from conf.db import connect
from conf.logging import ToolError
from conf.settings import ROOT
from scripts.archive import ArchiveError, Target, backup
from tests.integration.stack import ComposeStack
from tests.integration.test_resilience import eventually


def assert_no_archive_jobs(stack: ComposeStack, service: str) -> None:
    assert (
        stack.run(
            "exec",
            "-T",
            service,
            "bash",
            "-c",
            "compgen -G '/tmp/hw03-archive-*' || true",
        )
        == ""
    )


@pytest.mark.postgresql
@pytest.mark.parametrize("interrupt", [False, True], ids=["timeout", "sigint"])
def test_blocked_native_dump_is_stopped(
    stack: ComposeStack, tmp_path: Path, interrupt: bool
) -> None:
    config = stack.database_settings("PG")
    with connect(config) as connection:
        connection.execute("CREATE TABLE archive_timeout_probe (id INTEGER)")
    output = tmp_path / "backup.zip"
    try:
        with connect(config) as blocker, connect(config) as observer:
            blocker.execute("LOCK TABLE archive_timeout_probe IN ACCESS EXCLUSIVE MODE")

            def dump_is_running() -> bool:
                observer.rollback()
                return observer.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND application_name = 'pg_dump'"
                ).fetchone() != (0,)

            if interrupt:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "scripts.archive",
                        "backup",
                        "postgres",
                        str(output),
                        "--env-file",
                        str(stack.env_file),
                        "--project",
                        stack.project,
                    ],
                    cwd=ROOT,
                    env=stack.environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    eventually(dump_is_running)
                    process.send_signal(signal.SIGINT)
                    stdout, stderr = process.communicate(timeout=15)
                    assert process.returncode == 130
                    assert stdout == ""
                    assert "interrupted" in stderr
                    assert "Cannot confirm" not in stderr
                    assert all(secret not in stderr for secret in stack.passwords)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)
            else:
                target = Target(
                    stack.command, stack.environment, "PG", stack.passwords, timeout=1
                )
                with pytest.raises(ArchiveError, match="timed out"):
                    backup(target, output)
            # Keep the lock held: a detached pg_dump cannot finish on its own.
            eventually(lambda: not dump_is_running(), timeout=5)
            assert_no_archive_jobs(stack, "postgres")
        assert not output.exists()
        assert not list(tmp_path.glob(".hw03-backup-*"))
    finally:
        with connect(config) as connection:
            connection.execute("DROP TABLE archive_timeout_probe")


@pytest.mark.parametrize(
    "service",
    [
        pytest.param("postgres", marks=pytest.mark.postgresql),
        pytest.param("mongodb", marks=pytest.mark.mongodb),
    ],
)
def test_container_deadline_kills_stubborn_children_and_cleans_credentials(
    stack: ComposeStack, service: str
) -> None:
    token = uuid.uuid4().hex
    runner = ("exec", "-T", service, "bash", "/opt/hw03/archive-runner.sh")
    pid_file = f"/tmp/hw03-worker-{token}"
    stack.run(*runner, "prepare", token)
    body = (
        'trap "" TERM; printf "%s\\n" "$$" > "$1"; '
        'touch "$HW03_ARCHIVE_DIRECTORY/credentials.yaml"; '
        'sleep 60 & printf "%s\\n" "$!" >> "$1"; wait'
    )
    with pytest.raises(ToolError, match="timed out"):
        stack.run(*runner, "run", token, "1", "bash", "-c", body, "probe", pid_file)
    # Both the wrapper and its child must be gone or awaiting reaping, never running.
    stack.run(
        "exec",
        "-T",
        service,
        "bash",
        "-c",
        'while read -r pid; do state=$(ps -o stat= -p "$pid" || true); '
        '[[ -z $state || $state == Z* ]] || exit 1; done < "$1"; rm -- "$1"',
        "probe",
        pid_file,
    )
    assert_no_archive_jobs(stack, service)

    # Cancellation before a delayed exec starts must prevent the operation entirely.
    token = uuid.uuid4().hex
    stack.run(*runner, "prepare", token)
    stack.run(*runner, "cancel", token)
    with pytest.raises(ToolError, match="cancelled or not prepared"):
        stack.run(*runner, "run", token, "1", "touch", pid_file)
    stack.run("exec", "-T", service, "test", "!", "-e", pid_file)
    assert_no_archive_jobs(stack, service)
