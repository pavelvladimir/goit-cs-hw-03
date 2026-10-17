"""Validation before database access."""

import unittest
from unittest.mock import MagicMock

import main as cats
import seed


class ValidationTests(unittest.TestCase):
    def test_invalid_names_and_features(self) -> None:
        for value in (None, 42, "", " "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cats.clean_name(value)
        for features in (None, "рудий", [" "], [5]):
            with self.subTest(features=features), self.assertRaises(ValueError):
                cats.clean_features(features)
        self.assertEqual(cats.clean_features([" рудий ", "рудий"]), ["рудий"])
        self.assertEqual(cats.clean_features([]), [])
        self.assertEqual(cats.validate_age(0), 0)
        self.assertEqual(cats.validate_age(2**63 - 1), 2**63 - 1)

    def test_invalid_feature_does_not_reach_database(self) -> None:
        collection = MagicMock()
        for feature in (None, 1, " "):
            with self.subTest(feature=feature), self.assertRaises(ValueError):
                cats.add_cat_feature(collection, "barsik", feature)
        collection.update_one.assert_not_called()

    def test_seed_rejects_too_few_records(self) -> None:
        connection = MagicMock()
        for users, tasks in ((2, 30), (10, 2)):
            with self.subTest(users=users, tasks=tasks), self.assertRaises(ValueError):
                seed.seed_database(connection, users, tasks)
        connection.cursor.assert_not_called()
