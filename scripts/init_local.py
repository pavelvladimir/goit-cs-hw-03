"""Create missing configuration atomically; never replace existing credentials."""

import json
import os
import secrets
import stat
import sys
import tempfile
from io import StringIO
from pathlib import Path

from dotenv import dotenv_values

from conf.logging import report_error
from conf.settings import (
    ROOT,
    SECRET_KEYS,
    ConfigurationError,
    connection_parameters,
    read_secret,
    secret_path,
)


def check_file(path: Path, mode: int) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != mode:
        raise ConfigurationError(
            f"{json.dumps(str(path))}: потрібен звичайний файл з правами {mode:04o}."
        )
    return True


def check_directory(path: Path) -> None:
    if path.is_symlink() or (
        path.exists()
        and (not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700)
    ):
        raise ConfigurationError(
            f"{json.dumps(str(path))}: каталог має бути приватним (0700)."
        )


def create_file(path: Path, content: str, mode: int) -> bool:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".init-"
    ) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fchmod(temporary.fileno(), mode)
        try:
            os.link(temporary.name, path)
        except FileExistsError:
            check_file(path, mode)
            return False
    return True


def initialize(directory: Path) -> list[Path]:
    directory = directory.resolve()
    env_file = directory / ".env"
    exists = check_file(env_file, 0o600)
    source = env_file if exists else directory / ".env.example"
    try:
        content = source.read_text(encoding="utf-8")
    except OSError, UnicodeError:
        raise ConfigurationError(
            f"{json.dumps(str(source))}: не вдалося прочитати конфігурацію."
        ) from None
    values = {
        key: value
        for key, value in dotenv_values(
            stream=StringIO(content), interpolate=False
        ).items()
        if value is not None
    } | dict(os.environ)
    for prefix in ("PG", "MONGO"):
        connection_parameters(prefix, values)
    paths = [secret_path(values, key, directory) for key in SECRET_KEYS]
    secret_dir = directory / "secrets"
    check_directory(secret_dir)
    for key, path in zip(SECRET_KEYS, paths, strict=True):
        if path.parent != secret_dir:
            raise ConfigurationError(f"{key}: файл має бути безпосередньо в secrets/.")
    seen_paths: dict[Path, str] = {}
    for key, path in zip(SECRET_KEYS, paths, strict=True):
        if path in seen_paths:
            raise ConfigurationError(
                f"{key}: файл уже використовується {seen_paths[path]}."
            )
        seen_paths[path] = key
    passwords = []
    for key, path in zip(SECRET_KEYS, paths, strict=True):
        try:
            passwords.append(read_secret(path) if check_file(path, 0o444) else None)
        except ConfigurationError as error:
            raise ConfigurationError(f"{key}: {error}") from None
    seen_passwords: dict[str, str] = {}
    for key, value in zip(SECRET_KEYS, passwords, strict=True):
        if value is not None:
            if value in seen_passwords:
                raise ConfigurationError(
                    f"{key}: пароль має відрізнятися від {seen_passwords[value]}."
                )
            seen_passwords[value] = key
    created = []
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    if not exists and create_file(env_file, content, 0o600):
        created.append(env_file)
    for path, value in zip(paths, passwords, strict=True):
        if value is None and create_file(path, secrets.token_urlsafe(48) + "\n", 0o444):
            created.append(path)
    return created


def main() -> int:
    try:
        created = initialize(ROOT)
    except ConfigurationError as error:
        report_error("Помилка ініціалізації", error)
        return 1
    except OSError:
        print(
            "Помилка ініціалізації [FILESYSTEM]: перевірте права та вільне місце "
            "в каталозі проєкту.",
            file=sys.stderr,
        )
        return 1
    print(f"Локальна конфігурація готова. Створено файлів: {len(created)}.")
    print("Далі: docker compose up -d --wait")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
