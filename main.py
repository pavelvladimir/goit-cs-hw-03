"""Завдання 2: CRUD для колекції котів MongoDB через PyMongo."""

import argparse
import sys
from typing import TypedDict, cast

from bson import ObjectId, json_util
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.results import UpdateResult

from conf.db import mongo_client
from conf.logging import report_error
from conf.settings import settings


class CatFields(TypedDict):
    """Обов'язкові поля документа кота."""

    name: str
    age: int
    features: list[str]


class CatDocument(CatFields, total=False):
    """PyMongo додає _id автоматично під час вставлення."""

    _id: ObjectId


def clean_name(name: object) -> str:
    """Перевірити ім'я; пошук за ім'ям чутливий до регістру."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Ім'я не може бути порожнім.")
    return name.strip()


def validate_age(age: object) -> int:
    """Дозволити лише невід'ємний цілий вік у межах BSON int64."""
    if type(age) is not int or not 0 <= age <= 2**63 - 1:
        raise ValueError("Вік має бути невід'ємним цілим числом до 2^63 − 1.")
    return age


def clean_features(features: object) -> list[str]:
    """Перевірити й нормалізувати характеристики, прибрати повтори."""
    if not isinstance(features, list):
        raise ValueError("Характеристики мають бути списком непорожніх рядків.")
    cleaned: list[str] = []
    for feature in features:
        if not isinstance(feature, str) or not feature.strip():
            raise ValueError("Характеристики мають бути списком непорожніх рядків.")
        if feature.strip() not in cleaned:
            cleaned.append(feature.strip())
    return cleaned


def create_cat(
    collection: Collection[CatDocument], name: object, age: object, features: object
) -> ObjectId:
    """Create: додати кота; PyMongo автоматично додасть ObjectId перед insert_one."""
    document: CatDocument = {
        "name": clean_name(name),
        "age": validate_age(age),
        "features": clean_features(features),
    }
    return cast(ObjectId, collection.insert_one(document).inserted_id)


def read_all_cats(collection: Collection[CatDocument]) -> list[CatDocument]:
    """Read: отримати всі документи, впорядковані за ім'ям."""
    return list(collection.find().sort("name", 1))


def read_cat_by_name(
    collection: Collection[CatDocument], name: object
) -> CatDocument | None:
    """Read: знайти кота за ім'ям або повернути None."""
    return collection.find_one({"name": clean_name(name)})


def update_cat_age(
    collection: Collection[CatDocument], name: object, age: object
) -> UpdateResult:
    """Update: оновити вік; повернути кількість знайдених і змінених документів."""
    return collection.update_one(
        {"name": clean_name(name)}, {"$set": {"age": validate_age(age)}}
    )


def add_cat_feature(
    collection: Collection[CatDocument], name: object, feature: object
) -> UpdateResult:
    """Update: додати без повторів; повернути кількість знайдених і змінених котів."""
    if not isinstance(feature, str) or not feature.strip():
        raise ValueError("Характеристика не може бути порожньою.")
    return collection.update_one(
        {"name": clean_name(name)}, {"$addToSet": {"features": feature.strip()}}
    )


def delete_cat_by_name(collection: Collection[CatDocument], name: object) -> bool:
    """Delete: видалити кота за ім'ям; повернути, чи був запис."""
    return collection.delete_one({"name": clean_name(name)}).deleted_count > 0


def delete_all_cats(collection: Collection[CatDocument]) -> int:
    """Delete: видалити всі документи, повернути кількість видалених."""
    return collection.delete_many({}).deleted_count


def seed_cats(collection: Collection[CatDocument]) -> None:
    """Додати приклади, зберігаючи зміни в уже наявних документах."""
    samples: list[CatDocument] = [
        {
            "name": "barsik",
            "age": 3,
            "features": ["ходить в капці", "дає себе гладити", "рудий"],
        },
        {"name": "murka", "age": 2, "features": ["грайлива", "любить рибу"]},
        {"name": "tom", "age": 5, "features": ["сірий", "любить спати"]},
    ]
    for cat in samples:
        collection.update_one({"name": cat["name"]}, {"$setOnInsert": cat}, upsert=True)


def show_documents(documents: CatDocument | list[CatDocument] | None) -> None:
    """Вивести документи як JSON із коректним відображенням ObjectId."""
    if not documents:
        print("Записів не знайдено.")
    else:
        print(json_util.dumps(documents, ensure_ascii=False, indent=2))


def run_menu(collection: Collection[CatDocument]) -> None:
    """Зчитувати введення та обробляти помилки кожної операції."""
    menu = """
1 — Показати всіх котів
2 — Знайти кота за ім'ям
3 — Додати кота
4 — Оновити вік кота
5 — Додати характеристику
6 — Видалити кота за ім'ям
7 — Видалити всіх котів
0 — Вийти
"""
    while True:
        try:
            print(menu)
            choice = input("Оберіть дію: ").strip()
            if choice == "0":
                return
            if choice == "1":
                show_documents(read_all_cats(collection))
            elif choice == "2":
                show_documents(read_cat_by_name(collection, input("Ім'я: ")))
            elif choice == "3":
                name = input("Ім'я: ")
                age = int(input("Вік: "))
                features = [
                    item.strip()
                    for item in input(
                        "Характеристики через кому (можна залишити порожнім): "
                    ).split(",")
                    if item.strip()
                ]
                cat_id = create_cat(collection, name, age, features)
                print(f"Кота додано: {cat_id}")
            elif choice == "4":
                result = update_cat_age(
                    collection, input("Ім'я: "), int(input("Новий вік: "))
                )
                if result.matched_count == 0:
                    print("Кота не знайдено.")
                elif result.modified_count == 0:
                    print("Вік уже має вказане значення. Змін не внесено.")
                else:
                    print("Вік збережено.")
            elif choice == "5":
                result = add_cat_feature(
                    collection, input("Ім'я: "), input("Характеристика: ")
                )
                if result.matched_count == 0:
                    print("Кота не знайдено.")
                elif result.modified_count == 0:
                    print("Така характеристика вже є. Змін не внесено.")
                else:
                    print("Характеристику збережено.")
            elif choice == "6":
                found = delete_cat_by_name(collection, input("Ім'я: "))
                print("Кота видалено." if found else "Кота не знайдено.")
            elif choice == "7":
                if (
                    input("Для видалення всіх котів введіть DELETE: ").strip()
                    == "DELETE"
                ):
                    print(f"Видалено записів: {delete_all_cats(collection)}")
                else:
                    print("Видалення скасовано.")
            else:
                print("Невідома команда. Введіть число від 0 до 7.")
        except DuplicateKeyError:
            print("Кіт із таким ім'ям уже існує.", file=sys.stderr)
        except ValueError:
            print(
                "Некоректне введення: перевірте ім’я, вік і характеристики.",
                file=sys.stderr,
            )
        except PyMongoError as error:
            report_error("Помилка MongoDB", error)
        except EOFError, KeyboardInterrupt:
            print("\nРоботу завершено.")
            return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed", action="store_true", help="додати трьох котів і завершити"
    )
    args = parser.parse_args()
    try:
        config = settings("MONGO")
        client = mongo_client(config)
        with client:
            client.admin.command("ping")
            collection = cast(Collection[CatDocument], client[config.database]["cats"])
            # У цьому навчальному застосунку ім'я однозначно визначає кота.
            collection.create_index("name", unique=True)
            if args.seed:
                seed_cats(collection)
                print("Приклади котів додано.")
            else:
                run_menu(collection)
        return 0
    except (PyMongoError, ValueError) as error:
        report_error("Не вдалося запустити MongoDB-застосунок", error)
        return 1
    except KeyboardInterrupt:
        print("\nРоботу завершено.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
