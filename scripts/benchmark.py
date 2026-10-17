"""Порівняти плани двох SQL-запитів до й після індексів у тимчасовій PostgreSQL."""

import argparse
import hashlib
import json
import platform
import statistics
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from conf.db import connect
from conf.logging import report_error
from conf.settings import ROOT
from create_tables import create_tables
from scripts.queries import homework_queries
from scripts.stack import isolated_stack

INDEXES = (
    "CREATE INDEX tasks_user_id_id_idx ON tasks (user_id, id)",
    "CREATE INDEX tasks_status_id_id_idx ON tasks (status_id, id)",
)


def dataset(connection: psycopg.Connection[tuple[Any, ...]], rows: int) -> int:
    """Deterministic data, including a rare 'new' status and stable row width."""
    users = min(1000, max(10, rows // 100))
    connection.execute(
        "DROP INDEX IF EXISTS tasks_user_id_id_idx, tasks_status_id_id_idx"
    )
    connection.execute("TRUNCATE tasks, users, status RESTART IDENTITY")
    connection.execute(
        "INSERT INTO status (name) VALUES ('new'), ('in progress'), ('completed')"
    )
    connection.execute(
        "INSERT INTO users (fullname, email) "
        "SELECT 'User ' || g, 'user' || g || '@example.com' "
        "FROM generate_series(1, %s) g",
        (users,),
    )
    connection.execute(
        """INSERT INTO tasks (title, description, status_id, user_id)
        SELECT 'Task ' || g, repeat(md5(g::text), 4),
            CASE WHEN g %% 100 = 0 OR g = 1 THEN 1
                 WHEN g %% 4 = 0 THEN 2 ELSE 3 END,
            (g - 1) %% %s + 1
        FROM generate_series(1, %s) g""",
        (users, rows),
    )
    connection.execute("ANALYZE users; ANALYZE status; ANALYZE tasks")
    connection.commit()
    return users


def measure(
    connection: psycopg.Connection[tuple[Any, ...]], query: str, repeats: int
) -> dict[str, Any]:
    rows = connection.execute(query, prepare=False).fetchall()
    fingerprint = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False).encode()
    ).hexdigest()
    explain = "EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT JSON) " + query
    # One unrecorded EXPLAIN warm-up per query/phase. These are warm-cache samples.
    connection.execute(explain, prepare=False).fetchone()
    samples = []
    for _ in range(repeats):
        row = connection.execute(explain, prepare=False).fetchone()
        if row is None:
            raise RuntimeError("EXPLAIN returned no plan.")
        sample = row[0][0]
        if sample["Plan"]["Actual Rows"] != len(rows):
            raise RuntimeError("Plan row count differs from the actual result.")
        samples.append(sample)
    return {
        "rows": len(rows),
        "result_sha256": fingerprint,
        "median_execution_ms": statistics.median(s["Execution Time"] for s in samples),
        # Root totals already include descendants: do not sum the plan tree.
        "median_shared_blocks": statistics.median(
            s["Plan"].get("Shared Hit Blocks", 0)
            + s["Plan"].get("Shared Read Blocks", 0)
            for s in samples
        ),
        "samples": samples,
    }


def benchmark(
    connection: psycopg.Connection[tuple[Any, ...]],
    sizes: tuple[int, ...] = (30, 100_000),
    repeats: int = 5,
) -> dict[str, Any]:
    if not sizes or any(not 30 <= size <= 1_000_000 for size in sizes):
        raise ValueError("Dataset size must be between 30 and 1000000.")
    if not 3 <= repeats <= 30:
        raise ValueError("Use between 3 and 30 measured repetitions.")
    queries = homework_queries()[:2]
    create_tables(connection)
    connection.commit()
    server = connection.execute("SELECT version()").fetchone()
    settings = dict(
        connection.execute(
            "SELECT name, setting FROM pg_settings WHERE name IN "
            "('shared_buffers', 'work_mem', 'random_page_cost', 'seq_page_cost', "
            "'effective_cache_size', 'max_parallel_workers_per_gather', 'jit')"
        ).fetchall()
    )
    report: dict[str, Any] = {
        "format": 1,
        "created_utc": datetime.now(UTC).isoformat(),
        "server": server[0] if server else "unknown",
        "client_platform": platform.platform(),
        "python": platform.python_version(),
        "settings": settings,
        "method": (
            "warm cache; one EXPLAIN warm-up; medians; "
            "default planner; no timing thresholds"
        ),
        "repeats": repeats,
        "index_sql": list(INDEXES),
        "queries_sha256": hashlib.sha256(
            (ROOT / "queries.sql").read_bytes()
        ).hexdigest(),
        "datasets": [],
    }
    for size in sizes:
        users = dataset(connection, size)
        cases: list[dict[str, Any]] = []
        for query in queries:
            cases.append(
                {
                    "number": query.number,
                    "sql": query.sql,
                    "before": measure(connection, query.sql, repeats),
                }
            )
        for statement in INDEXES:
            connection.execute(statement)
        connection.commit()
        for case in cases:
            case["after"] = measure(connection, case["sql"], repeats)
            if case["before"]["result_sha256"] != case["after"]["result_sha256"]:
                raise RuntimeError("Index experiment changed query results.")
        index_bytes = dict(
            connection.execute(
                "SELECT indexrelname, pg_relation_size(indexrelid) "
                "FROM pg_stat_user_indexes WHERE indexrelname IN "
                "('tasks_user_id_id_idx', 'tasks_status_id_id_idx') "
                "ORDER BY indexrelname"
            ).fetchall()
        )
        report["datasets"].append(
            {
                "tasks": size,
                "users": users,
                "index_bytes": index_bytes,
                "queries": cases,
            }
        )
    return report


def summary(report: dict[str, Any]) -> str:
    lines = [
        "# Вимірювання SQL",
        "",
        "Медіани warm-cache EXPLAIN ANALYZE; час у ms, buffers — shared hit + read.",
        "",
        "| Tasks | SQL | Rows | Before ms | After ms "
        "| Before buffers | After buffers |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for data in report["datasets"]:
        for query in data["queries"]:
            before, after = query["before"], query["after"]
            lines.append(
                f"| {data['tasks']} | {query['number']} | {before['rows']} "
                f"| {before['median_execution_ms']:.3f} "
                f"| {after['median_execution_ms']:.3f} "
                f"| {before['median_shared_blocks']} "
                f"| {after['median_shared_blocks']} |"
            )
    lines.extend(
        ["", "Повні плани, параметри сервера й розміри індексів: results.json.", ""]
    )
    return "\n".join(lines)


def bounded_integer(minimum: int, maximum: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        result = int(value)
        if not minimum <= result <= maximum:
            raise argparse.ArgumentTypeError(
                f"Очікується число від {minimum} до {maximum}."
            )
        return result

    return parse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rows",
        type=bounded_integer(30, 1_000_000),
        default=100_000,
        metavar="30..1000000",
    )
    parser.add_argument(
        "--repeats", type=bounded_integer(3, 30), default=5, metavar="3..30"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts" / "benchmarks"
    )
    args = parser.parse_args()
    try:
        with isolated_stack("benchmark", ("postgres",)) as stack:
            with connect(stack.database_settings("PG")) as connection:
                sizes = (30,) if args.rows == 30 else (30, args.rows)
                report = benchmark(connection, sizes, args.repeats)
            report["compose_images"] = stack.run("config", "--images").splitlines()
            report["server_kernel"] = stack.run(
                "exec", "-T", "postgres", "uname", "-srm"
            ).strip()
            report["source_sha256"] = {
                path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                for path in ("scripts/benchmark.py", "create_tables.py", "compose.yaml")
            }
        # Publish only after successful measurements and Docker cleanup.
        output = args.output / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        output.mkdir(parents=True, exist_ok=False)
        (output / "results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (output / "summary.md").write_text(summary(report), encoding="utf-8")
        print(summary(report))
        print(f"Звіт: {output}")
        return 0
    except KeyboardInterrupt:
        print("[INTERRUPTED] Вимірювання перервано.", file=sys.stderr)
        return 130
    except (psycopg.Error, OSError, ValueError, RuntimeError) as error:
        report_error("Помилка вимірювання", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
