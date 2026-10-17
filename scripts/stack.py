"""Shared disposable Compose lifecycle for tests, demos and measurements."""

import os
import secrets
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Self

from dotenv import dotenv_values

from conf.logging import ToolError, redact
from conf.settings import ROOT, SECRET_KEYS, DatabaseSettings, from_values


def clean_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PG", "MONGO", "DB_", "COMPOSE_", "HW03_"))
        and key != "DATABASE_URL"
    }


def allocate_ports() -> tuple[str, str]:
    """Choose distinct loopback ports; Compose reports any competing bind as failure."""
    with socket.socket() as pg_port, socket.socket() as mongo_port:
        pg_port.bind(("127.0.0.1", 0))
        mongo_port.bind(("127.0.0.1", 0))
        return str(pg_port.getsockname()[1]), str(mongo_port.getsockname()[1])


def ignore_secrets(values: Iterable[str]) -> None:
    """Standalone tools have no pytest reporter to notify."""


@dataclass
class ComposeStack:
    secret_observer: ClassVar[Callable[[Iterable[str]], None]] = staticmethod(
        ignore_secrets
    )
    project: str
    env_file: Path
    command: list[str]
    environment: dict[str, str] = field(repr=False)
    passwords: tuple[str, ...] = field(repr=False)
    services: tuple[str, ...] = ("postgres", "mongodb")

    def __post_init__(self) -> None:
        type(self).secret_observer(self.passwords)

    def remember_password(self, password: str) -> None:
        self.passwords += (password,)
        type(self).secret_observer((password,))

    @classmethod
    def create(
        cls,
        directory: Path,
        services: tuple[str, ...] = ("postgres", "mongodb"),
        *,
        purpose: str = "test",
    ) -> Self:
        if not services or not set(services) <= {"postgres", "mongodb"}:
            raise ValueError("Select PostgreSQL, MongoDB, or both.")
        if purpose not in {"test", "demo", "benchmark"}:
            raise ValueError("Unknown disposable stack purpose.")
        project = f"hw03-{purpose}-" + secrets.token_hex(8)
        values = {
            key: value
            for key, value in dotenv_values(
                ROOT / ".env.example", interpolate=False
            ).items()
            if value is not None
        }
        # Docker may reassign an empty published port after stop/start. Select free
        # loopback ports once so an existing client can reconnect to the same address.
        # A competing bind fails Compose startup; we never attach to that service.
        values["HW03_PG_TEST_PORT"], values["HW03_MONGO_TEST_PORT"] = allocate_ports()
        private = directory / "secrets"
        with ExitStack() as rollback:
            private.mkdir(mode=0o700)
            rollback.callback(private.rmdir)
            passwords = []
            for key in SECRET_KEYS:
                # Exercise quoting paths, including spaces, quotes and URI delimiters.
                password = secrets.token_urlsafe(32) + "@:/?#%$&'\"\\ space Ж"
                cls.secret_observer((password,))
                path = private / key.lower()
                with path.open("x", encoding="utf-8") as stream:
                    rollback.callback(path.unlink, missing_ok=True)
                    stream.write(password + "\n")
                path.chmod(0o444)
                values[key] = str(path)
                passwords.append(password)
            env_file = directory / ".env"
            lines = []
            for key, value in values.items():
                escaped = value.replace("\\", "\\\\").replace("'", "\\'")
                lines.append(f"{key}='{escaped}'\n")
            with env_file.open("x", encoding="utf-8") as stream:
                rollback.callback(env_file.unlink, missing_ok=True)
                stream.write("".join(lines))
            env_file.chmod(0o600)
            instance = cls(
                project,
                env_file,
                [
                    "docker",
                    "compose",
                    "--project-directory",
                    str(ROOT),
                    "--env-file",
                    str(env_file),
                    "-p",
                    project,
                    "-f",
                    str(ROOT / "compose.yaml"),
                    "-f",
                    str(ROOT / "compose.test.yaml"),
                ],
                clean_environment(),
                tuple(passwords),
                services,
            )
            rollback.pop_all()
            return instance

    def start(self) -> None:
        self.run(
            "up", "-d", "--wait", "--wait-timeout", "120", *self.services, timeout=600
        )

    def run(self, *args: str, timeout: int = 180) -> str:
        try:
            result = subprocess.run(
                [*self.command, *args],
                env=self.environment,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=ROOT,
            )
        except OSError:
            raise ToolError(
                "Docker Compose unavailable; check Docker installation."
            ) from None
        except subprocess.TimeoutExpired:
            raise ToolError(
                f"Docker Compose {args[0]} timed out after {timeout} s."
            ) from None
        if result.returncode:
            detail = redact(result.stdout + result.stderr, self.passwords)[-6000:]
            raise ToolError(f"Compose {args[0]} failed:\n{detail}")
        return redact(result.stdout, self.passwords)

    def port(self, service: str, internal: int) -> str:
        address = self.run("port", service, str(internal)).strip()
        host, port = address.rsplit(":", 1)
        if host != "127.0.0.1" or not port.isdigit():
            raise ToolError("Expected an isolated loopback port.")
        return port

    def app_environment(self) -> dict[str, str]:
        values = self.environment | {"HW03_ENV_FILE": str(self.env_file)}
        for service in self.services:
            prefix, port = ("PG", 5432) if service == "postgres" else ("MONGO", 27017)
            values[prefix + "_HOST"] = "127.0.0.1"
            values[prefix + "_PORT"] = self.port(service, port)
        return values

    def database_settings(self, prefix: str) -> DatabaseSettings:
        values = {
            key: value
            for key, value in dotenv_values(self.env_file, interpolate=False).items()
            if value is not None
        }
        service, port = ("postgres", 5432) if prefix == "PG" else ("mongodb", 27017)
        if service not in self.services:
            raise ValueError("Database not selected for this isolated stack.")
        values[prefix + "_HOST"] = "127.0.0.1"
        values[prefix + "_PORT"] = self.port(service, port)
        return from_values(prefix, values, self.env_file.parent)

    def cleanup_files(self) -> None:
        """Remove only files created by this stack, after Docker cleanup succeeds."""
        private = self.env_file.parent / "secrets"
        for key in SECRET_KEYS:
            (private / key.lower()).unlink(missing_ok=True)
        private.rmdir()
        self.env_file.unlink(missing_ok=True)


@contextmanager
def running_stack(stack: ComposeStack) -> Iterator[ComposeStack]:
    try:
        stack.start()
        yield stack
    except BaseException:
        try:
            print(stack.run("logs", "--no-color", "--tail", "50"), file=sys.stderr)
        except RuntimeError:
            print("Container logs unavailable.", file=sys.stderr)
        raise
    finally:
        stack.run("down", "--volumes", "--remove-orphans", timeout=120)
        stack.cleanup_files()


@contextmanager
def isolated_stack(
    purpose: str, services: tuple[str, ...] = ("postgres", "mongodb")
) -> Iterator[ComposeStack]:
    """Keep recovery credentials if Docker cleanup fails; remove them on success."""
    directory = Path(tempfile.mkdtemp(prefix=f"hw03-{purpose}-"))
    stack = None
    try:
        stack = ComposeStack.create(directory, services, purpose=purpose)
        with running_stack(stack):
            yield stack
    finally:
        if stack is None or not stack.env_file.exists():
            directory.rmdir()
        else:
            print(f"Recovery configuration: {stack.env_file}", file=sys.stderr)
