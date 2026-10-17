"""Завдання 1: створення таблиць PostgreSQL без видалення наявних даних."""

import sys
from typing import Any

import psycopg

from conf.db import connect
from conf.logging import report_error
from conf.settings import ConfigurationError

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    fullname VARCHAR(100) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS status (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    title VARCHAR(100) NOT NULL,
    description TEXT,
    status_id INTEGER NOT NULL REFERENCES status(id),
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE
);
"""


def create_tables(connection: psycopg.Connection[tuple[Any, ...]]) -> None:
    """Створити три таблиці з унікальними полями та зовнішніми ключами."""
    connection.execute(SCHEMA)


def main() -> int:
    try:
        # Контекстний менеджер фіксує транзакцію або відкочує її при помилці.
        with connect() as connection:
            create_tables(connection)
        print("Таблиці users, status і tasks створено.")
        return 0
    except (psycopg.Error, ConfigurationError) as error:
        report_error("Помилка PostgreSQL", error)
        return 1
    except KeyboardInterrupt:
        print("[INTERRUPTED] Операцію перервано користувачем.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
