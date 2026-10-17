"""Real child transfers prove atomic publication and cleanup without Docker."""

import json
import os
import signal
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from conf.settings import ROOT
from scripts.archive import ArchiveError, Target, backup, main, restore
from tests.archive_helpers import rewrite_bundle

CHILD = """import json, os, sys, time
from pathlib import Path
mode = sys.argv[-1]
if "cancel" in sys.argv or "prepare" in sys.argv:
    if "cancel" in sys.argv and os.environ.get("CANCEL_FAIL"):
        print("private-marker", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
if mode == "info":
    print(json.dumps({"database": "test_db", "server_version": "17.11"}))
elif mode == "backup":
    sys.stdout.buffer.write(b"native-test-archive")
    sys.stdout.flush()
    if os.environ.get("DUMP_HANG"):
        Path(os.environ["CHILD_PID"]).write_text(str(os.getpid()))
        time.sleep(60)
    if os.environ.get("DUMP_FAIL"):
        print(os.environ["DUMP_FAIL"], file=sys.stderr)
        sys.exit(1)
    if os.environ.get("DUMP_DEADLINE"):
        sys.exit(124)
else:
    Path(os.environ["RESTORED"]).write_bytes(sys.stdin.buffer.read())
"""


def target(tmp_path: Path) -> Target:
    child = tmp_path / "transfer.py"
    child.write_text(CHILD)
    return Target(
        [sys.executable, str(child)],
        os.environ
        | {"RESTORED": str(tmp_path / "restored"), "CHILD_PID": str(tmp_path / "pid")},
        "PG",
        ("private-marker",),
        timeout=1,
    )


def test_bundle_round_trip_and_no_overwrite(tmp_path: Path) -> None:
    instance = target(tmp_path)
    bundle = tmp_path / "private" / "backup.zip"
    backup(instance, bundle)
    assert bundle.stat().st_mode & 0o777 == 0o600
    assert bundle.parent.stat().st_mode & 0o777 == 0o700
    original = bundle.read_bytes()
    with pytest.raises(ArchiveError, match="already exists"):
        backup(instance, bundle)
    assert bundle.read_bytes() == original
    assert list(bundle.parent.iterdir()) == [bundle]
    restore(instance, bundle)
    assert (tmp_path / "restored").read_bytes() == b"native-test-archive"
    with zipfile.ZipFile(bundle) as zipped:
        manifest = json.loads(zipped.read("manifest.json"))
        assert manifest["database"] == "test_db"
        assert manifest["server_version"] == "17.11"
        assert len(manifest["sha256"]) == 64


@pytest.mark.parametrize(
    "change",
    [
        "checksum",
        "size",
        "database",
        "version",
        "engine",
        "format",
        "invalid",
        "entries",
        "truncated",
    ],
)
def test_invalid_bundle_never_reaches_restore(tmp_path: Path, change: str) -> None:
    instance = target(tmp_path)
    bundle, bad = tmp_path / "backup.zip", tmp_path / "bad.zip"
    backup(instance, bundle)
    if change == "truncated":
        bad.write_bytes(bundle.read_bytes()[:30])
    else:
        rewrite_bundle(bundle, bad, change)
    (tmp_path / "restored").write_text("existing data")
    with pytest.raises(ArchiveError):
        restore(instance, bad)
    assert (tmp_path / "restored").read_text() == "existing data"


@pytest.mark.parametrize("entry", ["manifest.json", "archive"])
@pytest.mark.parametrize("corrupted", [False, True])
def test_compressed_bundle_is_rejected_before_reading_or_connecting(
    local_config: Path,
    entry: str,
    corrupted: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original, compressed = (
        local_config / "original.zip",
        local_config / "compressed.zip",
    )
    backup(target(local_config), original)
    with (
        zipfile.ZipFile(original) as source,
        zipfile.ZipFile(compressed, "w") as output,
    ):
        for name in source.namelist():
            output.writestr(
                name,
                source.read(name),
                compress_type=zipfile.ZIP_DEFLATED
                if name == entry
                else zipfile.ZIP_STORED,
            )
    if corrupted:
        with zipfile.ZipFile(compressed) as archive:
            offset = archive.getinfo(entry).header_offset
        data = bytearray(compressed.read_bytes())
        name_length, extra_length = struct.unpack_from("<HH", data, offset + 26)
        # Invalid DEFLATE block type: reading this entry would raise zlib.error.
        data[offset + 30 + name_length + extra_length] = 0x06
        compressed.write_bytes(data)
    with patch.object(
        Target, "run", side_effect=AssertionError("Unexpected DB access")
    ):
        assert (
            main(
                [
                    "restore",
                    "postgres",
                    str(compressed),
                    "--env-file",
                    str(local_config / ".env"),
                    "--project",
                    "test",
                ]
            )
            == 1
        )
    diagnostic = capsys.readouterr()
    assert diagnostic.out == ""
    assert "Unsupported backup compression" in diagnostic.err
    assert "Traceback" not in diagnostic.err
    assert not (local_config / "restored").exists()


@pytest.mark.parametrize("failure", ["exit", "timeout", "deadline", "cancel_failed"])
def test_failed_dump_is_not_published(tmp_path: Path, failure: str) -> None:
    instance = target(tmp_path)
    key = {"exit": "DUMP_FAIL", "deadline": "DUMP_DEADLINE"}.get(failure, "DUMP_HANG")
    instance.environment[key] = "private-marker"
    if failure == "cancel_failed":
        instance.environment["CANCEL_FAIL"] = "1"
    output = tmp_path / "backup.zip"
    with pytest.raises(ArchiveError) as error:
        backup(instance, output)
    assert "private-marker" not in str(error.value)
    if failure == "cancel_failed":
        assert "Cannot confirm" in str(error.value)
    elif failure in {"timeout", "deadline"}:
        assert "timed out" in str(error.value)
    assert not output.exists()
    assert not list(tmp_path.glob(".hw03-backup-*"))
    if failure in {"timeout", "cancel_failed"}:
        with pytest.raises(ProcessLookupError):
            os.kill(int((tmp_path / "pid").read_text()), 0)


@pytest.mark.parametrize(
    "failure", [FileNotFoundError(), subprocess.TimeoutExpired("docker", 8)]
)
def test_failed_container_preparation_does_not_publish_backup(
    tmp_path: Path, failure: Exception
) -> None:
    instance = target(tmp_path)
    with patch("scripts.archive.subprocess.run", side_effect=failure):
        with pytest.raises(ArchiveError, match="Cannot confirm"):
            backup(instance, tmp_path / "backup.zip")
    assert not (tmp_path / "backup.zip").exists()
    assert not list(tmp_path.glob(".hw03-backup-*"))


def test_sigint_cleans_partial_dump(tmp_path: Path) -> None:
    instance = target(tmp_path)
    script = tmp_path / "parent.py"
    script.write_text(
        "from pathlib import Path\nfrom scripts.archive import Target, backup\n"
        "import os, sys\n"
        "target = Target([sys.executable, sys.argv[1]], dict(os.environ), 'PG')\n"
        "backup(target, Path(sys.argv[2]))\n"
    )
    output = tmp_path / "backup.zip"
    environment = instance.environment | {"DUMP_HANG": "1", "PYTHONPATH": str(ROOT)}
    process = subprocess.Popen(
        [sys.executable, str(script), instance.command[1], str(output)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "pid").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (tmp_path / "pid").exists()
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=5)
        assert process.returncode != 0
        assert not output.exists()
        assert not list(tmp_path.glob(".hw03-backup-*"))
        with pytest.raises(ProcessLookupError):
            os.kill(int((tmp_path / "pid").read_text()), 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize("mode", ["backup", "restore"])
@pytest.mark.parametrize("engine", ["postgres", "mongodb"])
def test_cli_rejects_non_utf8_configuration_before_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, engine: str
) -> None:
    env_file = tmp_path / "invalid.env"
    env_file.write_bytes(b"PG_DB=task_manager\nPRIVATE_MARKER=secret-value\n\xff")
    bundle = tmp_path / "backup.zip"
    if mode == "restore":
        bundle.write_bytes(b"existing archive")
    # A real child CLI must never reach Docker while decoding this configuration.
    marker = tmp_path / "docker-called"
    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\nraise SystemExit(99)\n"
    )
    docker.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.archive",
            mode,
            engine,
            str(bundle),
            "--env-file",
            str(env_file),
            "--project",
            "invalid-env-test",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "Configuration file must use UTF-8.\n"
    assert not marker.exists()
    if mode == "restore":
        assert bundle.read_bytes() == b"existing archive"
    else:
        assert not bundle.exists()
    assert not list(tmp_path.glob(".hw03-backup-*"))


def test_cli_requires_explicit_target_and_reports_file_failure(tmp_path: Path) -> None:
    assert (
        main(
            [
                "backup",
                "postgres",
                str(tmp_path / "a.zip"),
                "--env-file",
                str(tmp_path / "missing"),
                "--project",
                "test",
            ]
        )
        == 1
    )
    assert (
        main(
            [
                "backup",
                "postgres",
                str(tmp_path / "a.zip"),
                "--env-file",
                str(tmp_path / "missing"),
                "--project",
                "!bad",
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "exception, code", [(ArchiveError("safe failure"), 1), (KeyboardInterrupt(), 130)]
)
def test_cli_error_exit_codes(
    tmp_path: Path, exception: BaseException, code: int
) -> None:
    secret = tmp_path / "secret"
    secret.write_text("password")
    env = tmp_path / ".env"
    env.write_text(f"PG_PASSWORD_FILE={secret}\nPG_ADMIN_PASSWORD_FILE={secret}\n")
    with patch("scripts.archive.backup", side_effect=exception):
        assert (
            main(
                [
                    "backup",
                    "postgres",
                    str(tmp_path / "a.zip"),
                    "--env-file",
                    str(env),
                    "--project",
                    "test",
                ]
            )
            == code
        )


@pytest.mark.parametrize("engine,prefix", [("postgres", "PG"), ("mongodb", "MONGO")])
def test_cli_uses_explicit_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str, prefix: str
) -> None:
    binary = tmp_path / "docker"
    program = CHILD.replace(
        "mode = sys.argv[-1]",
        'mode = next((a.split("=", 1)[1] for a in sys.argv '
        'if a.startswith("HW03_ARCHIVE_MODE=")), sys.argv[-1])',
    )
    program = program.replace(
        "mode = next",
        'assert not any(k.startswith(("PG", "MONGO", "DB_", "COMPOSE_", "HW03_")) '
        "for k in os.environ)\n"
        'assert sys.argv[sys.argv.index("-p") + 1] == "explicit-project"\n'
        "mode = next",
    )
    binary.write_text(f"#!{sys.executable}\n" + program)
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("RESTORED", str(tmp_path / "restored"))
    monkeypatch.setenv("PG_DB", "unrelated_database")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "unrelated-project")
    secret = tmp_path / "password"
    secret.write_text("private-marker")
    env = tmp_path / ".env"
    env.write_text(
        f"{prefix}_PASSWORD_FILE={secret}\n{prefix}_ADMIN_PASSWORD_FILE={secret}\n"
    )
    bundle = tmp_path / "bundle.zip"
    args = [
        engine,
        str(bundle),
        "--env-file",
        str(env),
        "--project",
        "explicit-project",
    ]
    assert main(["backup", *args]) == 0
    assert main(["restore", *args]) == 0
    assert (tmp_path / "restored").read_bytes() == b"native-test-archive"


@pytest.mark.parametrize("metadata", [b"not-json", b"[]", b'{"database": 1}'])
def test_invalid_server_metadata_prevents_dump(tmp_path: Path, metadata: bytes) -> None:
    instance = target(tmp_path)
    with patch.object(instance, "run", return_value=metadata):
        with pytest.raises(ArchiveError, match="metadata"):
            backup(instance, tmp_path / "backup.zip")
    assert not list(tmp_path.glob(".hw03-backup-*"))


def test_concurrent_destination_cannot_be_overwritten(tmp_path: Path) -> None:
    instance = target(tmp_path)
    bundle = tmp_path / "backup.zip"
    link = os.link

    def competing_publisher(source: Path, destination: Path) -> None:
        destination.write_bytes(b"previous backup")
        link(source, destination)

    with patch("scripts.archive.os.link", side_effect=competing_publisher):
        with pytest.raises(FileExistsError):
            backup(instance, bundle)
    assert bundle.read_bytes() == b"previous backup"
    assert not list(tmp_path.glob(".hw03-backup-*"))


def test_empty_successful_dump_is_not_published(tmp_path: Path) -> None:
    instance = target(tmp_path)
    child = Path(instance.command[1])
    child.write_text(CHILD.replace('b"native-test-archive"', 'b""'))
    with pytest.raises(ArchiveError, match="empty"):
        backup(instance, tmp_path / "backup.zip")
    assert not (tmp_path / "backup.zip").exists()
    assert not list(tmp_path.glob(".hw03-backup-*"))
