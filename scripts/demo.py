"""Відтворювана демонстрація SQL і MongoDB у власних тимчасових базах."""

import argparse
import json
import sys
from typing import Any, cast

import psycopg
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

import main as cats
from conf.db import connect, mongo_client
from conf.logging import report_error
from conf.settings import ROOT
from create_tables import create_tables
from scripts.queries import homework_queries
from scripts.stack import isolated_stack

DEMO_DATA = """
INSERT INTO users (fullname, email) VALUES
    ('Alice', 'a@example.com'), ('Bob', 'b@example.org'), ('Carol', 'c@example.com');
INSERT INTO status (name) VALUES ('new'), ('in progress'), ('completed');
INSERT INTO tasks (title, description, status_id, user_id) VALUES
    ('First', NULL, 1, 1), ('Second', 'Done', 3, 1),
    ('Third', '', 2, 2), ('Fourth', '   ', 1, 2);
"""


def postgres_demo(connection: psycopg.Connection[tuple[Any, ...]]) -> None:
    """Execute the actual homework file, including its transaction and mutations."""
    create_tables(connection)
    connection.execute(DEMO_DATA)
    connection.commit()
    queries = homework_queries()
    print("PostgreSQL: 3 користувачі, 3 статуси, 4 завдання.")
    with connection.cursor() as cursor:
        cursor.execute(
            (ROOT / "queries.sql").read_text(encoding="utf-8"), prepare=False
        )
        index = 0
        while True:
            if cursor.statusmessage not in ("BEGIN", "COMMIT"):
                query = queries[index]
                print(f"\nSQL {query.number:02}: {query.title}")
                if cursor.description:
                    columns = [column.name for column in cursor.description]
                    for row in cursor.fetchall():
                        print(
                            json.dumps(
                                dict(zip(columns, row, strict=True)), ensure_ascii=False
                            )
                        )
                else:
                    print(f"Змінено рядків: {cursor.rowcount}")
                index += 1
            if not cursor.nextset():
                break
    if index != 14:
        raise RuntimeError("Expected all 14 homework query results.")


def show_cats(label: str, documents: list[cats.CatDocument]) -> None:
    # Insert and upsert assign real ObjectIds. Display stable business fields only.
    values = [
        {key: doc[key] for key in ("name", "age", "features")} for doc in documents
    ]
    print(label + ": " + json.dumps(values, ensure_ascii=False))


def mongo_demo(collection: Collection[cats.CatDocument]) -> None:
    collection.create_index("name", unique=True)
    cats.seed_cats(collection)
    print("\nMongoDB: документи мають ObjectId; показано name, age, features.")
    show_cats("Seed / Read all", cats.read_all_cats(collection))
    cats.create_cat(collection, "simba", 1, ["грайливий"])
    document = cats.read_cat_by_name(collection, "simba")
    show_cats("Create / Read simba", [document] if document else [])
    cats.update_cat_age(collection, "simba", 2)
    cats.add_cat_feature(collection, "simba", "рудий")
    cats.add_cat_feature(collection, "simba", "рудий")
    document = cats.read_cat_by_name(collection, "simba")
    show_cats("Update age / Add feature двічі", [document] if document else [])
    print(f"Delete simba: {cats.delete_cat_by_name(collection, 'simba')}")
    show_cats("Read remaining", cats.read_all_cats(collection))
    print(f"Delete all: {cats.delete_all_cats(collection)}")
    show_cats("Read empty", cats.read_all_cats(collection))


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        with isolated_stack("demo") as stack:
            with connect(stack.database_settings("PG")) as connection:
                postgres_demo(connection)
            config = stack.database_settings("MONGO")
            with mongo_client(config) as client:
                collection = cast(
                    Collection[cats.CatDocument], client[config.database].cats
                )
                mongo_demo(collection)
        print("\nДемонстрацію завершено; тимчасові ресурси прибрано.")
        return 0
    except KeyboardInterrupt:
        print("[INTERRUPTED] Демонстрацію перервано.", file=sys.stderr)
        return 130
    except (psycopg.Error, PyMongoError, OSError, ValueError, RuntimeError) as error:
        report_error("Помилка демонстрації", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
