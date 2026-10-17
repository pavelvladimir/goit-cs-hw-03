"""Завдання 1: наповнення PostgreSQL випадковими даними за допомогою Faker."""

import random
import sys
from typing import Any
from uuid import uuid4

import psycopg
from faker import Faker

from conf.db import connect
from conf.logging import report_error

STATUSES = ("new", "in progress", "completed")


def seed_database(
    connection: psycopg.Connection[tuple[Any, ...]],
    users_count: int = 10,
    tasks_count: int = 30,
) -> None:
    """Додати дані; два останні користувачі залишаються без завдань."""
    if users_count < 3 or tasks_count < 3:
        raise ValueError("Потрібні щонайменше 3 користувачі та 3 завдання.")

    fake = Faker("uk_UA")
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO status (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            [(name,) for name in STATUSES],
        )
        cursor.execute("SELECT name, id FROM status")
        status_ids: dict[str, int] = dict(cursor.fetchall())

        user_ids: list[int] = []
        for index in range(users_count):
            # UUID дозволяє повторний запуск без конфлікту унікальності email.
            domain = "example.com" if index % 2 == 0 else "example.org"
            email = f"{fake.user_name()[:30]}.{uuid4().hex}@{domain}"
            cursor.execute(
                "INSERT INTO users (fullname, email) VALUES (%s, %s) RETURNING id",
                (fake.name()[:100], email),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Не отримано ID створеного користувача.")
            user_ids.append(row[0])

        active_users = user_ids[:-2]
        for index in range(tasks_count):
            # Гарантовані приклади всіх статусів і відсутнього опису.
            description = (
                None if index % 5 == 0 else "" if index % 5 == 1 else fake.text()
            )
            cursor.execute(
                """
                INSERT INTO tasks (title, description, status_id, user_id)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    fake.sentence(nb_words=5)[:100],
                    description,
                    status_ids[STATUSES[index % len(STATUSES)]],
                    active_users[index]
                    if index < len(active_users)
                    else random.choice(active_users),
                ),
            )


def main() -> int:
    try:
        with connect() as connection:
            seed_database(connection)
        print("Додано 10 користувачів і 30 завдань. Статуси готові.")
        return 0
    except (psycopg.Error, ValueError) as error:
        report_error("Помилка наповнення", error)
        return 1
    except KeyboardInterrupt:
        print("[INTERRUPTED] Операцію перервано користувачем.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
