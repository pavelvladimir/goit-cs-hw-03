"""Real independent clients contend at a barrier; verify durable outcomes."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import cast

import psycopg
import pytest
from bson import ObjectId
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

import main as cats
from conf.db import connect, mongo_client
from create_tables import create_tables
from tests.integration.stack import ComposeStack


def race[T](operation: Callable[[int, Barrier], T]) -> list[T]:
    barrier = Barrier(2, timeout=10)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(operation, index, barrier) for index in range(2)]
        return [future.result(timeout=20) for future in futures]


@pytest.mark.postgresql
@pytest.mark.parametrize("round_number", range(3))
def test_unique_email_race_rolls_back_losing_transaction(
    stack: ComposeStack, round_number: int
) -> None:
    config = stack.database_settings("PG")
    with connect(config) as connection:
        create_tables(connection)
        connection.execute("TRUNCATE tasks, users, status RESTART IDENTITY")

    def insert(index: int, barrier: Barrier) -> tuple[int, str, int]:
        with connect(config) as connection:
            pid = connection.info.backend_pid
            try:
                with connection.transaction():
                    # This preceding write must also disappear if the email loses.
                    connection.execute(
                        "INSERT INTO status (name) VALUES (%s)", (f"writer_{index}",)
                    )
                    barrier.wait()
                    connection.execute(
                        "INSERT INTO users (fullname, email) VALUES (%s, %s)",
                        (f"Writer {index}", f"race{round_number}@example.com"),
                    )
            except psycopg.errors.UniqueViolation:
                return index, "duplicate", pid
            return index, "created", pid

    outcomes = race(insert)
    assert sorted(outcome for _, outcome, _ in outcomes) == ["created", "duplicate"]
    assert len({pid for _, _, pid in outcomes}) == 2
    winner = next(index for index, outcome, _ in outcomes if outcome == "created")
    with connect(config) as connection:
        assert connection.execute("SELECT fullname, email FROM users").fetchall() == [
            (f"Writer {winner}", f"race{round_number}@example.com")
        ]
        assert connection.execute("SELECT name FROM status").fetchall() == [
            (f"writer_{winner}",)
        ]


@pytest.mark.mongodb
@pytest.mark.parametrize("round_number", range(3))
def test_unique_cat_name_has_exactly_one_winner(
    stack: ComposeStack, round_number: int
) -> None:
    config = stack.database_settings("MONGO")
    name = f"race-{round_number}"
    with mongo_client(config) as client:
        collection = cast(
            Collection[cats.CatDocument], client[config.database].race_cats
        )
        collection.drop()
        collection.create_index("name", unique=True)
        try:

            def insert(index: int, barrier: Barrier) -> tuple[int, str]:
                with mongo_client(config) as worker:
                    worker.admin.command("ping")
                    target = cast(
                        Collection[cats.CatDocument], worker[config.database].race_cats
                    )
                    barrier.wait()
                    try:
                        cats.create_cat(target, name, index, [f"writer_{index}"])
                    except DuplicateKeyError:
                        return index, "duplicate"
                    return index, "created"

            outcomes = race(insert)
            assert sorted(result for _, result in outcomes) == ["created", "duplicate"]
            winner = next(index for index, result in outcomes if result == "created")
            documents = cats.read_all_cats(collection)
            assert len(documents) == 1
            assert isinstance(documents[0]["_id"], ObjectId)
            assert documents[0]["name"] == name
            assert documents[0]["age"] == winner
            assert documents[0]["features"] == [f"writer_{winner}"]
        finally:
            collection.drop()


@pytest.mark.mongodb
@pytest.mark.parametrize("same_feature", [True, False])
def test_concurrent_features_neither_duplicate_nor_lose_updates(
    stack: ComposeStack, same_feature: bool
) -> None:
    config = stack.database_settings("MONGO")
    features = ("рудий", "рудий" if same_feature else "лагідний")
    with mongo_client(config) as client:
        collection = cast(
            Collection[cats.CatDocument], client[config.database].race_cats
        )
        collection.drop()
        collection.create_index("name", unique=True)
        cats.create_cat(collection, "barsik", 3, ["грайливий"])
        try:

            def update(index: int, barrier: Barrier) -> tuple[int, int]:
                with mongo_client(config) as worker:
                    worker.admin.command("ping")
                    target = cast(
                        Collection[cats.CatDocument], worker[config.database].race_cats
                    )
                    barrier.wait()
                    result = cats.add_cat_feature(target, "barsik", features[index])
                    return result.matched_count, result.modified_count

            expected = [(1, 0), (1, 1)] if same_feature else [(1, 1), (1, 1)]
            assert sorted(race(update)) == expected
            document = cats.read_cat_by_name(collection, "barsik")
            assert document is not None
            assert document["age"] == 3
            assert set(document["features"]) == {"грайливий", *features}
            assert len(document["features"]) == len(set(document["features"]))
        finally:
            collection.drop()
