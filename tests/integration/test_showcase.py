"""Demonstration output and real query plans on isolated Percona servers."""

from typing import cast

import pytest
from pymongo.collection import Collection

import main as cats
from conf.db import connect, mongo_client
from create_tables import create_tables
from scripts.benchmark import benchmark, summary
from scripts.demo import mongo_demo, postgres_demo
from tests.integration.stack import ComposeStack


@pytest.mark.postgresql
def test_demo_executes_all_homework_queries_reproducibly(
    stack: ComposeStack, capsys: pytest.CaptureFixture[str]
) -> None:
    outputs = []
    for _ in range(2):
        with connect(stack.database_settings("PG")) as connection:
            create_tables(connection)
            connection.execute("TRUNCATE tasks, users, status RESTART IDENTITY")
            connection.commit()
            postgres_demo(connection)
            assert connection.execute("SELECT count(*) FROM tasks").fetchone() == (4,)
            assert connection.execute(
                "SELECT fullname FROM users WHERE id=1"
            ).fetchone() == ("Олександр Коваленко",)
            outputs.append(capsys.readouterr().out)
    assert outputs[0] == outputs[1]
    assert outputs[0].count("\nSQL ") == 14
    assert outputs[0].count("Змінено рядків: 1") == 4
    assert '"name": "completed", "task_count": 0' in outputs[0]
    assert '"fullname": "Carol", "task_count": 0' in outputs[0]


@pytest.mark.mongodb
def test_demo_covers_crud_and_produces_stable_output(
    stack: ComposeStack, capsys: pytest.CaptureFixture[str]
) -> None:
    config = stack.database_settings("MONGO")
    outputs = []
    with mongo_client(config) as client:
        collection = cast(
            Collection[cats.CatDocument], client[config.database].demo_cats
        )
        try:
            for _ in range(2):
                collection.drop()
                mongo_demo(collection)
                assert collection.count_documents({}) == 0
                outputs.append(capsys.readouterr().out)
        finally:
            collection.drop()
    assert outputs[0] == outputs[1]
    assert '"age": 2, "features": ["грайливий", "рудий"]' in outputs[0]
    assert "Delete simba: True" in outputs[0]
    assert "Delete all: 3" in outputs[0]
    assert "Read empty: []" in outputs[0]
    assert "документи мають ObjectId" in outputs[0]
    assert "_id створює сервер" not in outputs[0]


@pytest.mark.postgresql
def test_measurement_keeps_results_and_records_real_plans(stack: ComposeStack) -> None:
    with connect(stack.database_settings("PG")) as connection:
        report = benchmark(connection, sizes=(30, 5000), repeats=3)
    assert report["repeats"] == 3
    assert "PostgreSQL 17" in report["server"]
    assert len(report["datasets"]) == 2
    for data, expected_rows in zip(
        report["datasets"], ((3, 1), (100, 51)), strict=True
    ):
        assert len(data["index_bytes"]) == 2
        assert all(size > 0 for size in data["index_bytes"].values())
        for query, expected in zip(data["queries"], expected_rows, strict=True):
            assert query["before"]["result_sha256"] == query["after"]["result_sha256"]
            for phase in ("before", "after"):
                result = query[phase]
                assert result["rows"] == expected
                assert len(result["samples"]) == 3
                assert result["median_execution_ms"] >= 0
                assert result["median_shared_blocks"] > 0
                for sample in result["samples"]:
                    assert sample["Plan"]["Actual Rows"] == expected
                    assert "Node Type" in sample["Plan"]
    assert "| 5000 | 1 | 100 |" in summary(report)
