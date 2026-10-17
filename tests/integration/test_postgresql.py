"""PostgreSQL schema, fourteen queries, roles and rollback."""

import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from psycopg import sql

import seed
from conf.db import connect
from conf.settings import DatabaseSettings
from create_tables import create_tables
from seed import seed_database
from tests.integration.stack import ComposeStack

pytestmark = pytest.mark.postgresql


class PostgreSQLTests(unittest.TestCase):
    config: DatabaseSettings

    @pytest.fixture(autouse=True)
    def configure(self, stack: ComposeStack) -> None:
        self.config = stack.database_settings("PG")

    def setUp(self) -> None:
        self.connection = connect(self.config)
        self.addCleanup(self.connection.close)
        create_tables(self.connection)
        self.connection.execute("TRUNCATE tasks, users, status RESTART IDENTITY")
        self.connection.commit()

    def fixture(self) -> None:
        self.connection.execute("""
            INSERT INTO users (fullname, email) VALUES
                ('Alice', 'a@example.com'), ('Bob', 'b@example.org'),
                ('Carol', 'c@example.com');
            INSERT INTO status (name) VALUES ('new'), ('in progress'), ('completed');
            INSERT INTO tasks (title, description, status_id, user_id) VALUES
                ('First', NULL, 1, 1), ('Second', 'Done', 3, 1),
                ('Third', '', 2, 2), ('Fourth', '   ', 1, 2);
        """)
        self.connection.commit()

    def test_schema_is_repeatable(self) -> None:
        self.fixture()
        create_tables(self.connection)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM tasks").fetchall()[0][0], 4
        )

    def test_unique_and_foreign_key_constraints(self) -> None:
        self.fixture()
        cases = [
            (
                "INSERT INTO users (fullname, email) "
                "VALUES ('Duplicate', 'a@example.com')",
                psycopg.errors.UniqueViolation,
            ),
            (
                "INSERT INTO status (name) VALUES ('new')",
                psycopg.errors.UniqueViolation,
            ),
            (
                "INSERT INTO tasks (title, status_id, user_id) "
                "VALUES ('Bad user', 1, 999)",
                psycopg.errors.ForeignKeyViolation,
            ),
            (
                "INSERT INTO tasks (title, status_id, user_id) "
                "VALUES ('Bad status', 999, 1)",
                psycopg.errors.ForeignKeyViolation,
            ),
        ]
        for query, exception in cases:
            with self.subTest(query=query):
                with self.assertRaises(exception):
                    with self.connection.transaction():
                        self.connection.execute(query)

    def test_delete_user_cascades_only_their_tasks(self) -> None:
        self.fixture()
        self.connection.execute("DELETE FROM users WHERE id = 1")
        self.assertEqual(
            self.connection.execute("SELECT id FROM tasks ORDER BY id").fetchall(),
            [(3,), (4,)],
        )

    def test_seed_includes_required_examples_and_can_repeat(self) -> None:
        seed_database(self.connection)
        for table, expected in (("users", 10), ("tasks", 30), ("status", 3)):
            count = self.connection.execute(
                sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
            ).fetchall()[0][0]
            self.assertEqual(count, expected)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM users WHERE id NOT IN (SELECT user_id FROM tasks)"
            ).fetchall()[0][0],
            2,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(DISTINCT status_id) FROM tasks"
            ).fetchall()[0][0],
            3,
        )
        self.assertGreater(
            self.connection.execute(
                "SELECT COUNT(*) FROM tasks "
                "WHERE description IS NULL OR description = ''"
            ).fetchall()[0][0],
            0,
        )
        seed_database(self.connection)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM users").fetchall()[0][0], 20
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM tasks").fetchall()[0][0], 60
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM status").fetchall()[0][0], 3
        )

    def test_all_fourteen_queries_and_their_results(self) -> None:
        self.fixture()
        query_file = Path(__file__).resolve().parents[2] / "queries.sql"
        results: dict[int, list[tuple[Any, ...]]] = {}
        affected: dict[int, int] = {}
        index = 0
        with self.connection.cursor() as cursor:
            cursor.execute(query_file.read_text(encoding="utf-8"), prepare=False)
            while True:
                if cursor.statusmessage not in ("BEGIN", "COMMIT"):
                    if cursor.description:
                        results[index] = cursor.fetchall()
                    else:
                        affected[index] = cursor.rowcount
                    index += 1
                if not cursor.nextset():
                    break
        self.assertEqual(len(results) + len(affected), 14)
        self.assertEqual([row[0] for row in results[0]], [1, 2])
        self.assertEqual([row[0] for row in results[1]], [1, 4])
        self.assertEqual(affected[2], 1)
        self.assertEqual([row[0] for row in results[3]], [3])
        self.assertEqual(affected[4], 1)
        self.assertEqual([row[0] for row in results[5]], [1, 3, 4, 5])
        self.assertEqual(affected[6], 1)
        self.assertEqual([row[0] for row in results[7]], [1, 3])
        self.assertEqual(affected[8], 1)
        self.assertEqual(dict(results[9]), {"new": 2, "in progress": 2, "completed": 0})
        self.assertEqual([row[0] for row in results[10]], [1, 5])
        self.assertEqual([row[0] for row in results[11]], [1, 3, 4])
        self.assertEqual([(row[0], row[2]) for row in results[12]], [(1, 1), (2, 3)])
        self.assertEqual(
            [(row[0], row[2]) for row in results[13]], [(1, 2), (2, 2), (3, 0)]
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT fullname FROM users WHERE id = 1"
            ).fetchall()[0][0],
            "Олександр Коваленко",
        )


def test_postgresql_role_and_table_ownership(stack: ComposeStack) -> None:
    config = stack.database_settings("PG")
    with connect(config) as connection:
        create_tables(connection)
        assert connection.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user"
        ).fetchone() == (False, False, False, False, False)
        assert connection.execute(
            "SELECT DISTINCT tableowner FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename IN ('users', 'status', 'tasks')"
        ).fetchall() == [(config.user,)]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connection.transaction():
                connection.execute("CREATE ROLE forbidden_role")
    with pytest.raises(psycopg.OperationalError):
        connect(replace(config, password="wrong-password", timeout=1))


def test_seed_rolls_back_every_insert_on_midway_failure(stack: ComposeStack) -> None:
    config = stack.database_settings("PG")
    with connect(config) as connection:
        create_tables(connection)
        connection.execute("TRUNCATE tasks, users, status RESTART IDENTITY")
    with pytest.raises(RuntimeError, match="intentional"):
        with connect(config) as connection:
            # Fail on the second task, after statuses, users and one task were inserted.
            with patch.object(seed, "Faker") as factory:
                fake = factory.return_value
                fake.user_name.return_value = "test"
                fake.name.return_value = "Test User"
                fake.sentence.side_effect = ["first", RuntimeError("intentional")]
                seed.seed_database(connection)
    with connect(config) as connection:
        assert connection.execute(
            "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM tasks), "
            "(SELECT count(*) FROM status)"
        ).fetchone() == (0, 0, 0)
