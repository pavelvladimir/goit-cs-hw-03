"""MongoDB CRUD, menu behavior and authorization."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from typing import cast
from unittest.mock import patch

import pytest
from bson import ObjectId
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError, OperationFailure

import main as cats
from conf.db import mongo_client
from conf.settings import DatabaseSettings
from tests.integration.stack import ComposeStack

pytestmark = pytest.mark.mongodb


class MongoDBTests(unittest.TestCase):
    config: DatabaseSettings

    @pytest.fixture(autouse=True)
    def configure(self, stack: ComposeStack) -> None:
        self.config = stack.database_settings("MONGO")

    def setUp(self) -> None:
        config = self.config
        self.client = mongo_client(config)
        self.addCleanup(self.client.close)
        self.collection = cast(
            Collection[cats.CatDocument], self.client[config.database]["cats"]
        )
        self.collection.create_index("name", unique=True)
        self.collection.delete_many({})

    def test_create_read_and_document_structure(self) -> None:
        cat_id = cats.create_cat(self.collection, " barsik ", 3, ["рудий"])
        self.assertIsInstance(cat_id, ObjectId)
        cat = cats.read_cat_by_name(self.collection, "barsik")
        self.assertEqual(
            cat, {"_id": cat_id, "name": "barsik", "age": 3, "features": ["рудий"]}
        )
        self.assertEqual(cats.read_all_cats(self.collection), [cat])
        self.assertIsNone(cats.read_cat_by_name(self.collection, "missing"))

    def test_update_and_feature_deduplication(self) -> None:
        cats.create_cat(self.collection, "barsik", 3, ["рудий"])
        self.assertTrue(cats.update_cat_age(self.collection, "barsik", 4))
        self.assertTrue(cats.update_cat_age(self.collection, "barsik", 4))
        self.assertTrue(cats.add_cat_feature(self.collection, "barsik", "лагідний"))
        cats.add_cat_feature(self.collection, "barsik", "лагідний")
        cat = cats.read_cat_by_name(self.collection, "barsik")
        assert cat is not None
        self.assertEqual(cat["age"], 4)
        self.assertEqual(cat["features"], ["рудий", "лагідний"])
        self.assertFalse(cats.update_cat_age(self.collection, "missing", 2))
        self.assertFalse(cats.add_cat_feature(self.collection, "missing", "рудий"))

    def test_delete_one_and_delete_all(self) -> None:
        cats.seed_cats(self.collection)
        self.assertTrue(cats.delete_cat_by_name(self.collection, "barsik"))
        self.assertFalse(cats.delete_cat_by_name(self.collection, "barsik"))
        self.assertEqual(cats.delete_all_cats(self.collection), 2)
        self.assertEqual(cats.read_all_cats(self.collection), [])
        self.assertEqual(cats.delete_all_cats(self.collection), 0)

    def test_seed_preserves_existing_edits(self) -> None:
        cats.seed_cats(self.collection)
        cats.update_cat_age(self.collection, "barsik", 8)
        cats.seed_cats(self.collection)
        self.assertEqual(self.collection.count_documents({}), 3)
        cat = cats.read_cat_by_name(self.collection, "barsik")
        assert cat is not None
        self.assertEqual(cat["age"], 8)

    def test_menu_crud_and_missing_records(self) -> None:
        inputs = [
            "3",
            "leo",
            "2",
            "рудий, лагідний, рудий",
            "1",
            "2",
            "leo",
            "2",
            "missing",
            "4",
            "leo",
            "4",
            "4",
            "missing",
            "1",
            "5",
            "leo",
            "грає",
            "5",
            "missing",
            "грає",
            "6",
            "leo",
            "6",
            "missing",
            "unknown",
            "0",
        ]
        output = io.StringIO()
        with patch("builtins.input", side_effect=inputs), redirect_stdout(output):
            cats.run_menu(self.collection)
        for message in (
            "Кота додано",
            "Вік збережено",
            "Характеристику збережено",
            "Кота видалено",
            "Кота не знайдено",
            "Записів не знайдено",
            "Невідома команда",
        ):
            self.assertIn(message, output.getvalue())
        self.assertEqual(self.collection.count_documents({}), 0)

    def test_main_seed_and_interactive_mode(self) -> None:
        with redirect_stdout(io.StringIO()):
            with patch("sys.argv", ["main.py", "--seed"]):
                self.assertEqual(cats.main(), 0)
            with (
                patch("sys.argv", ["main.py"]),
                patch("builtins.input", side_effect=["1", "0"]),
            ):
                self.assertEqual(cats.main(), 0)
        self.assertEqual(self.collection.count_documents({}), 3)

    def test_menu_duplicate_and_interruption(self) -> None:
        cats.create_cat(self.collection, "leo", 2, [])
        output = io.StringIO()
        with (
            patch("builtins.input", side_effect=["3", "leo", "3", "", EOFError]),
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            cats.run_menu(self.collection)
        self.assertIn("уже існує", output.getvalue())
        self.assertIn("Роботу завершено", output.getvalue())

    def test_invalid_data_and_duplicate_name(self) -> None:
        for age in (-1, 1.5, True, "three", 2**63):
            with self.subTest(age=age), self.assertRaises(ValueError):
                cats.create_cat(self.collection, "barsik", age, [])
        for name, features in ((" ", []), ("barsik", "рудий"), ("barsik", [""])):
            with (
                self.subTest(name=name, features=features),
                self.assertRaises(ValueError),
            ):
                cats.create_cat(self.collection, name, 3, features)
        cats.create_cat(self.collection, "barsik", 3, [])
        with self.assertRaises(DuplicateKeyError):
            cats.create_cat(self.collection, "barsik", 4, [])

    def test_menu_handles_invalid_input_and_database_errors(self) -> None:
        output = io.StringIO()
        with (
            patch("builtins.input", side_effect=["4", "barsik", "oops", "1", "0"]),
            patch.object(
                cats, "read_all_cats", side_effect=OperationFailure("Test error")
            ),
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            cats.run_menu(self.collection)
        self.assertIn("Некоректне введення", output.getvalue())
        self.assertIn("Помилка MongoDB", output.getvalue())

    def test_delete_all_requires_menu_confirmation(self) -> None:
        cats.seed_cats(self.collection)
        with (
            patch("builtins.input", side_effect=["7", "no", "0"]),
            redirect_stdout(io.StringIO()),
        ):
            cats.run_menu(self.collection)
        self.assertEqual(self.collection.count_documents({}), 3)
        with (
            patch("builtins.input", side_effect=["7", "DELETE", "0"]),
            redirect_stdout(io.StringIO()),
        ):
            cats.run_menu(self.collection)
        self.assertEqual(self.collection.count_documents({}), 0)


def test_mongo_authorization(stack: ComposeStack) -> None:
    config = stack.database_settings("MONGO")
    with mongo_client(config) as client:
        client[config.database].cats.find_one({})
        for action in (
            lambda: client["forbidden_db"].cats.insert_one({"name": "denied"}),
            lambda: client.admin.command("usersInfo"),
        ):
            with pytest.raises(OperationFailure) as error:
                action()
            assert error.value.code == 13
    with mongo_client(replace(config, password="wrong-password", timeout=1)) as client:
        with pytest.raises(OperationFailure) as error:
            client[config.database].cats.find_one({})
        assert error.value.code == 18
