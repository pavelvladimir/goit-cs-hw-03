"""Round-trip real native archives into independently bootstrapped databases."""

from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from pymongo.errors import DuplicateKeyError, OperationFailure

from conf.db import connect, mongo_client
from create_tables import create_tables
from scripts.archive import ArchiveError, Target, backup, restore
from seed import seed_database
from tests.archive_helpers import rewrite_bundle
from tests.integration.stack import ComposeStack, running_stack


def archive_target(stack: ComposeStack, prefix: str) -> Target:
    return Target(stack.command, stack.environment, prefix, stack.passwords)


def pg_snapshot(stack: ComposeStack) -> dict[str, list[tuple[Any, ...]]]:
    with connect(stack.database_settings("PG")) as connection:
        return {
            table: connection.execute(
                sql.SQL("SELECT * FROM {} ORDER BY id").format(sql.Identifier(table))
            ).fetchall()
            for table in ("users", "status", "tasks")
        }


def assert_pg_constraints_and_role(stack: ComposeStack) -> None:
    config = stack.database_settings("PG")
    with connect(config) as connection:
        assert connection.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles "
            "WHERE rolname = current_user"
        ).fetchone() == (False, False, False)
        assert connection.execute(
            "SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall() == [(config.user,)]
        with pytest.raises(psycopg.errors.UniqueViolation):
            with connection.transaction():
                connection.execute("INSERT INTO status (name) VALUES ('new')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO users (fullname, email) "
                    "SELECT fullname, email FROM users LIMIT 1"
                )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO tasks (title, status_id, user_id) "
                    "VALUES ('invalid', -1, -1)"
                )
        # A real application write also verifies restored sequence positions.
        user = connection.execute(
            "INSERT INTO users (fullname, email) "
            "VALUES ('Restored', 'restore@example.com') RETURNING id"
        ).fetchone()
        assert user is not None and user[0] > 10
        connection.execute(
            "INSERT INTO tasks (title, status_id, user_id) SELECT 'cascade', id, %s "
            "FROM status WHERE name = 'new'",
            (user[0],),
        )
        connection.execute("DELETE FROM users WHERE id = %s", (user[0],))
        assert connection.execute(
            "SELECT count(*) FROM tasks WHERE user_id = %s", (user[0],)
        ).fetchone() == (0,)
        connection.rollback()


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("PG", marks=pytest.mark.postgresql),
        pytest.param("MONGO", marks=pytest.mark.mongodb),
    ],
)
def test_native_archive_round_trip(
    stack: ComposeStack, prefix: str, tmp_path: Path
) -> None:
    config = stack.database_settings(prefix)
    if prefix == "PG":
        with connect(config) as connection:
            create_tables(connection)
            connection.execute("TRUNCATE tasks, users, status RESTART IDENTITY CASCADE")
            seed_database(connection)
        original_pg = pg_snapshot(stack)
    else:
        with mongo_client(config) as client:
            cats = client[config.database].cats
            cats.drop()
            cats.create_index("name", unique=True)
            cats.insert_many(
                [
                    {"name": "Архів", "age": 7, "features": ["unicode", "відновлений"]},
                    {"name": "Empty features", "age": 0, "features": []},
                ]
            )
            original_mongo = list(cats.find().sort("name"))
            original_indexes = cats.index_information()
    archive = tmp_path / "backup.zip"
    backup(archive_target(stack, prefix), archive)
    assert archive.stat().st_size > 0
    assert archive.stat().st_mode & 0o777 == 0o600
    target_directory = tmp_path / "restore Арк'ів target"
    target_directory.mkdir()
    target = ComposeStack.create(target_directory, services=stack.services)
    assert not set(stack.passwords) & set(target.passwords)
    with running_stack(target):
        restore(archive_target(target, prefix), archive)
        restored = target.database_settings(prefix)
        if prefix == "PG":
            assert pg_snapshot(target) == original_pg
            assert_pg_constraints_and_role(target)
        else:
            with mongo_client(restored) as client:
                cats = client[restored.database].cats
                assert list(cats.find().sort("name")) == original_mongo
                assert cats.index_information() == original_indexes
                with pytest.raises(DuplicateKeyError):
                    cats.insert_one({"name": "Архів"})
                with pytest.raises(OperationFailure) as error:
                    client.admin.command("usersInfo")
                assert error.value.code == 13
                cats.update_one({"name": "Архів"}, {"$set": {"age": 8}})
                assert cats.find_one({"name": "Архів", "age": 8}) is not None
                cats.update_one({"name": "Архів"}, {"$set": {"age": 7}})
        # An accidental second restore must fail before changing existing target data.
        for change in ("checksum", "truncated", "database"):
            bad = tmp_path / (change + ".zip")
            if change == "truncated":
                bad.write_bytes(archive.read_bytes()[:30])
            else:
                rewrite_bundle(archive, bad, change)
            with pytest.raises(ArchiveError):
                restore(archive_target(target, prefix), bad)
        with pytest.raises(ArchiveError, match="not empty"):
            restore(archive_target(target, prefix), archive)
        if prefix == "PG":
            assert pg_snapshot(target) == original_pg
            assert pg_snapshot(stack) == original_pg
        else:
            with mongo_client(restored) as client:
                assert (
                    list(client[restored.database].cats.find().sort("name"))
                    == original_mongo
                )
            with mongo_client(config) as client:
                assert (
                    list(client[config.database].cats.find().sort("name"))
                    == original_mongo
                )
