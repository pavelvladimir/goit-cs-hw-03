"""Publish private, checked native database archives; restore only verified bundles."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from dotenv import dotenv_values

from conf.logging import redact
from conf.settings import ROOT, ConfigurationError, read_secret, secret_path


class ArchiveError(ValueError):
    """A failed transfer or an invalid backup; safe to show to the operator."""


@dataclass
class Target:
    command: list[str]
    environment: dict[str, str] = field(repr=False)
    prefix: str
    passwords: tuple[str, ...] = field(default=(), repr=False)
    timeout: int = 120

    def arguments(self, mode: str, token: str) -> list[str]:
        service = "postgres" if self.prefix == "PG" else "mongodb"
        base = [
            "exec",
            "-T",
            "-e",
            "HW03_ARCHIVE_MODE=" + mode,
            service,
            "bash",
            "/opt/hw03/archive-runner.sh",
        ]
        if mode in {"prepare", "cancel"}:
            return [*base, mode, token]
        command = (
            ["bash", "/opt/hw03/archive-postgres.sh", mode]
            if self.prefix == "PG"
            else ["mongosh", "--quiet", "--norc", "/opt/hw03/archive-mongo.js"]
        )
        return [*base, "run", token, str(self.timeout), *command]

    def control(self, mode: str, token: str) -> None:
        """Prepare/cancel only this operation, including cancellation before startup."""
        try:
            result = subprocess.run(
                [*self.command, *self.arguments(mode, token)],
                env=self.environment,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=8,
            )
        except OSError, subprocess.TimeoutExpired:
            raise ArchiveError(f"Cannot confirm container archive {mode}.") from None
        if result.returncode:
            raise ArchiveError(f"Cannot confirm container archive {mode}.")

    def run(self, mode: str, stream: BinaryIO | None = None) -> bytes:
        token = uuid.uuid4().hex
        try:
            self.control("prepare", token)
            result = subprocess.run(
                [*self.command, *self.arguments(mode, token)],
                env=self.environment,
                cwd=ROOT,
                stdin=stream if mode == "restore" else subprocess.DEVNULL,
                stdout=stream if mode == "backup" else subprocess.PIPE,
                stderr=subprocess.PIPE,
                # Allow the container deadline, forced kill, and wrapper cleanup first.
                timeout=self.timeout + 5,
            )
        except subprocess.TimeoutExpired:
            self.control("cancel", token)
            raise ArchiveError("Archive command timed out.") from None
        except KeyboardInterrupt:
            try:
                self.control("cancel", token)
            except ArchiveError as error:
                print(str(error), file=sys.stderr)
            raise
        except OSError:
            raise ArchiveError("Archive command unavailable.") from None
        if result.returncode == 124:
            raise ArchiveError(
                "Archive command timed out; container process terminated."
            )
        if result.returncode:
            # mongosh console.error can arrive on stdout; dumps themselves go to file.
            diagnostic = (result.stdout or b"") + result.stderr
            detail = redact(diagnostic.decode(errors="replace"), self.passwords)
            raise ArchiveError("Archive command failed: " + detail[-4000:])
        return result.stdout or b""

    def info(self) -> dict[str, str]:
        try:
            value = json.loads(self.run("info"))
        except json.JSONDecodeError, UnicodeError:
            raise ArchiveError("Invalid database metadata.") from None
        if not isinstance(value, dict) or any(
            not isinstance(value.get(key), str) or not value[key]
            for key in ("database", "server_version")
        ):
            raise ArchiveError("Invalid database metadata.")
        return {key: value[key] for key in ("database", "server_version")}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def backup(target: Target, destination: Path) -> None:
    """Publish one complete bundle with an atomic, no-overwrite hard link."""
    if destination.exists() or destination.is_symlink():
        raise ArchiveError("Backup destination already exists.")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(
        prefix=".hw03-backup-", dir=destination.parent
    ) as raw:
        directory = Path(raw)  # mkdtemp is private even with a permissive umask.
        archive, bundle = directory / "archive", directory / "bundle"
        metadata = target.info()
        with archive.open("xb") as stream:
            archive.chmod(0o600)
            target.run("backup", stream)
        if not archive.stat().st_size:
            raise ArchiveError("Database dump was empty.")
        manifest = metadata | {
            "format": 1,
            "engine": target.prefix,
            "created_at": datetime.now(UTC).isoformat(),
            "sha256": digest(archive),
            "bytes": archive.stat().st_size,
        }
        with bundle.open("xb") as output:
            bundle.chmod(0o600)
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as zipped:
                zipped.write(archive, "archive")
                zipped.writestr("manifest.json", json.dumps(manifest) + "\n")
            output.flush()
            os.fsync(output.fileno())
        # Same filesystem; a concurrent creator cannot be overwritten.
        os.link(bundle, destination)


def unpack(bundle: Path, archive: Path) -> dict[str, object]:
    try:
        with zipfile.ZipFile(bundle) as zipped:
            if sorted(zipped.namelist()) != ["archive", "manifest.json"]:
                raise ArchiveError("Unexpected backup entries.")
            # Our bundle uses ZIP_STORED; native dump tools handle compression.
            if any(
                item.compress_type != zipfile.ZIP_STORED for item in zipped.infolist()
            ):
                raise ArchiveError(
                    "Unsupported backup compression; expected ZIP_STORED."
                )
            if zipped.getinfo("manifest.json").file_size > 16384:
                raise ArchiveError("Backup manifest too large.")
            manifest = json.loads(zipped.read("manifest.json"))
            if not isinstance(manifest, dict) or manifest.get("format") != 1:
                raise ArchiveError("Unsupported backup manifest.")
            if (
                manifest.get("engine") not in ("PG", "MONGO")
                or any(
                    not isinstance(manifest.get(k), str) or not manifest[k]
                    for k in ("database", "server_version", "created_at", "sha256")
                )
                or type(manifest.get("bytes")) is not int
                or manifest["bytes"] <= 0
            ):
                raise ArchiveError("Invalid backup manifest.")
            with zipped.open("archive") as source, archive.open("xb") as output:
                archive.chmod(0o600)
                shutil.copyfileobj(source, output)
        if (
            archive.stat().st_size != manifest["bytes"]
            or digest(archive) != manifest["sha256"]
        ):
            raise ArchiveError("Backup checksum or size mismatch.")
        return manifest
    except (zipfile.BadZipFile, ValueError, KeyError, RuntimeError) as error:
        if isinstance(error, ArchiveError):
            raise
        raise ArchiveError("Invalid or truncated backup bundle.") from None


def restore(target: Target, bundle: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="hw03-restore-") as raw:
        archive = Path(raw) / "archive"
        manifest = unpack(bundle, archive)
        if manifest["engine"] != target.prefix:
            raise ArchiveError("Backup database engine differs from target.")
        info = target.info()
        if manifest["database"] != info["database"]:
            raise ArchiveError("Backup database name differs from target.")
        # This is a recovery tool, not a cross-major migration tool.
        if (
            str(manifest["server_version"]).split(".")[0]
            != info["server_version"].split(".")[0]
        ):
            raise ArchiveError("Backup server major version differs from target.")
        with archive.open("rb") as stream:
            target.run("restore", stream)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("backup", "restore"))
    parser.add_argument("engine", choices=("postgres", "mongodb"))
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.project):
            raise ArchiveError("Invalid Compose project name.")
        env_file = args.env_file.resolve(strict=True)
        try:
            values = {
                k: v
                for k, v in dotenv_values(env_file, interpolate=False).items()
                if v is not None
            }
        except UnicodeError:
            raise ConfigurationError("Configuration file must use UTF-8.") from None
        prefix = "PG" if args.engine == "postgres" else "MONGO"
        passwords = tuple(
            read_secret(secret_path(values, prefix + suffix, ROOT))
            for suffix in ("_PASSWORD_FILE", "_ADMIN_PASSWORD_FILE")
        )
        environment = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("PG", "MONGO", "DB_", "COMPOSE_", "HW03_"))
            and k != "DATABASE_URL"
        }
        target = Target(
            [
                "docker",
                "compose",
                "--project-directory",
                str(ROOT),
                "--env-file",
                str(env_file),
                "-p",
                args.project,
                "-f",
                str(ROOT / "compose.yaml"),
            ],
            environment,
            prefix,
            passwords,
        )
        (backup if args.mode == "backup" else restore)(target, args.bundle)
    except (ArchiveError, ConfigurationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        print("Cannot read or publish the backup/configuration files.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Archive operation interrupted.", file=sys.stderr)
        return 130
    print("Backup published." if args.mode == "backup" else "Restore completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
