"""Driver factories that pass credentials as parameters, never interpolated URIs."""

from typing import Any

import psycopg
from pymongo import MongoClient

from conf.settings import DatabaseSettings, settings


def connect(
    config: DatabaseSettings | None = None,
) -> psycopg.Connection[tuple[Any, ...]]:
    config = config or settings("PG")
    return psycopg.connect(
        host=config.host,
        port=config.port,
        dbname=config.database,
        user=config.user,
        password=config.password,
        connect_timeout=config.timeout,
        options=(
            f"-c statement_timeout={config.query_timeout * 1000} "
            f"-c lock_timeout={config.query_timeout * 1000}"
        ),
    )


def mongo_client(config: DatabaseSettings) -> MongoClient[dict[str, Any]]:
    return MongoClient(
        host=config.host,
        port=config.port,
        username=config.user,
        password=config.password,
        authSource=config.database,
        serverSelectionTimeoutMS=config.timeout * 1000,
        connectTimeoutMS=config.timeout * 1000,
        socketTimeoutMS=config.timeout * 1000,
        timeoutMS=config.query_timeout * 1000,
    )
