"""Atomic local configuration and filesystem permissions."""

import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from conf.settings import (
    ROOT,
    SECRET_KEYS,
    ConfigurationError,
    read_values,
)
from scripts import init_local


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PG_USER", "postgres"),
        ("PG_USER", "public"),
        ("PG_USER", "none"),
        ("PG_DB", "template0"),
        ("PG_DB", "template1"),
        ("MONGO_USER", "root"),
        ("PG_DB", "pg_catalog"),
        ("MONGO_DB", "admin"),
        ("PG_PORT", "70000"),
        ("MONGO_PORT", "not-a-port"),
        ("PG_HOST", "host/path"),
        ("MONGO_HOST", "host/path"),
        ("DB_TIMEOUT", "31"),
        ("DB_QUERY_TIMEOUT", "0"),
        ("PG_PASSWORD", "private-marker"),
        ("MONGO_PASSWORD", "private-marker"),
    ],
)
def test_invalid_connection_fields_fail_before_creating_files(
    tmp_path: Path,
    key: str,
    value: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    example = tmp_path / ".env.example"
    example.write_text((ROOT / ".env.example").read_text() + f"\n{key}={value}\n")
    original = example.read_bytes()
    with patch.object(init_local, "ROOT", tmp_path):
        assert init_local.main() == 1
    diagnostic = capsys.readouterr()
    assert diagnostic.out == ""
    assert "[CONFIG]" in diagnostic.err
    assert key in diagnostic.err
    assert "private-marker" not in diagnostic.err
    assert list(tmp_path.iterdir()) == [example]
    assert example.read_bytes() == original


@pytest.mark.parametrize("user", ["public", "none"])
@pytest.mark.parametrize("mode", [[], ["--rotate-app-password"]])
def test_bootstrap_rejects_reserved_role_before_reading_secrets(
    user: str, mode: list[str]
) -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "docker/init-postgres.sh"), *mode],
        env=dict(os.environ) | {"PG_APP_USER": user},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "Invalid PostgreSQL application role.\n"


def test_invalid_override_does_not_repair_or_change_existing_configuration(
    local_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = local_config / "secrets/mongo_password"
    missing.unlink()
    files = [local_config / ".env", *sorted((local_config / "secrets").iterdir())]

    def snapshot() -> dict[Path, tuple[bytes, int, int, int]]:
        return {
            path: (
                path.read_bytes(),
                path.stat().st_mode,
                path.stat().st_ino,
                path.stat().st_mtime_ns,
            )
            for path in files
        }

    before = snapshot()
    monkeypatch.setenv("DB_QUERY_TIMEOUT", "0")
    with pytest.raises(ConfigurationError, match="DB_QUERY_TIMEOUT"):
        init_local.initialize(local_config)
    assert not missing.exists()
    assert snapshot() == before


def test_init_is_repeatable_and_credentials_are_private(local_config: Path) -> None:
    paths = [local_config / ".env", *sorted((local_config / "secrets").iterdir())]
    before = {
        path: (
            path.read_bytes(),
            path.stat().st_mode,
            path.stat().st_ino,
            path.stat().st_mtime_ns,
        )
        for path in paths
    }
    assert init_local.initialize(local_config) == []
    assert before == {
        path: (
            path.read_bytes(),
            path.stat().st_mode,
            path.stat().st_ino,
            path.stat().st_mtime_ns,
        )
        for path in paths
    }
    assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600
    assert stat.S_IMODE((local_config / "secrets").stat().st_mode) == 0o700
    assert len({path.read_bytes() for path in paths[1:]}) == 4
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o444 for path in paths[1:])
    paths[1].unlink()
    assert init_local.initialize(local_config) == [paths[1]]
    assert paths[0].read_bytes() == before[paths[0]][0]


@pytest.mark.parametrize("target", [".env", "secrets", "secrets/pg_password"])
def test_init_rejects_insecure_modes(local_config: Path, target: str) -> None:
    path = local_config / target
    path.chmod(0o777)
    with pytest.raises(ConfigurationError):
        init_local.initialize(local_config)
    assert stat.S_IMODE(path.stat().st_mode) == 0o777


def test_init_rejects_symlink_without_changing_target(local_config: Path) -> None:
    target = local_config / "target"
    target.write_text("do not change")
    secret = local_config / "secrets/pg_password"
    secret.unlink()
    secret.symlink_to(target)
    with pytest.raises(ConfigurationError):
        init_local.initialize(local_config)
    assert target.read_text() == "do not change"


@pytest.mark.parametrize("value", ["secrets/shared", "../outside", "$HOME/password"])
def test_init_rejects_shared_or_external_paths(
    local_config: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    for key in SECRET_KEYS:
        monkeypatch.setenv(key, value)
    with pytest.raises(ConfigurationError):
        init_local.initialize(local_config)


def test_init_rejects_duplicate_passwords(local_config: Path) -> None:
    source = local_config / "secrets/pg_password"
    target = local_config / "secrets/pg_admin_password"
    target.chmod(0o600)
    target.write_bytes(source.read_bytes())
    target.chmod(0o444)
    with pytest.raises(ConfigurationError):
        init_local.initialize(local_config)


def test_atomic_create_does_not_replace_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "atomic"
    assert init_local.create_file(path, "original", 0o600)
    assert not init_local.create_file(path, "replacement", 0o600)
    assert path.read_text() == "original"
    assert not list(tmp_path.glob(".init-*"))


def test_init_cli_and_errors(
    local_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(init_local, "ROOT", local_config)
    assert init_local.main() == 0
    (local_config / ".env").chmod(0o644)
    assert init_local.main() == 1
    assert "0600" in capsys.readouterr().err
    with patch.object(init_local, "initialize", side_effect=OSError("secret-value")):
        assert init_local.main() == 1
        assert "secret-value" not in capsys.readouterr().err


def test_missing_or_invalid_dotenv(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        init_local.initialize(tmp_path)
    path = tmp_path / "bad.env"
    path.write_bytes(b"\xff")
    with pytest.raises(ConfigurationError):
        read_values(path)


def test_parallel_initializers_preserve_files_in_directory_with_spaces(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "project with spaces"
    directory.mkdir()
    (directory / ".env.example").write_bytes((ROOT / ".env.example").read_bytes())
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; "
        "from scripts.init_local import initialize; initialize(Path(sys.argv[1]))",
        str(directory),
    ]
    for _ in range(2):
        processes = [
            subprocess.Popen(
                command,
                cwd=ROOT,
                env=dict(os.environ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(8)
        ]
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=20)
                assert process.returncode == 0, stderr
                assert stdout == ""
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        paths = [directory / ".env", *sorted((directory / "secrets").iterdir())]
        current = {
            path: (
                path.read_bytes(),
                path.stat().st_mode,
                path.stat().st_ino,
                path.stat().st_mtime_ns,
            )
            for path in paths
        }
        if _ == 0:
            before = current
        else:
            assert current == before
        assert len(paths) == 5
        assert not list(directory.rglob(".init-*"))
        assert init_local.initialize(directory) == []


def test_init_diagnostic_names_key_without_secret_contents(local_config: Path) -> None:
    path = local_config / "secrets/pg_password"
    path.chmod(0o600)
    path.write_text("secret-first-line\nsecret-second-line")
    path.chmod(0o444)
    with pytest.raises(ConfigurationError, match="PG_PASSWORD_FILE") as error:
        init_local.initialize(local_config)
    assert "secret-first-line" not in str(error.value)


def test_init_path_diagnostic_escapes_control_characters(tmp_path: Path) -> None:
    path = tmp_path / "bad\npath"
    path.touch(mode=0o600)
    with pytest.raises(ConfigurationError) as error:
        init_local.check_file(path, 0o444)
    assert "bad\\npath" in str(error.value)
    assert "\n" not in str(error.value)
