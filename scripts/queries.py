"""Read the numbered homework sections without splitting SQL string literals."""

import re
from dataclasses import dataclass

from conf.settings import ROOT


@dataclass(frozen=True)
class Query:
    number: int
    title: str
    sql: str


def homework_queries() -> list[Query]:
    source = (ROOT / "queries.sql").read_text(encoding="utf-8")
    headings = list(re.finditer(r"^-- (\d+)\. (.+)$", source, re.MULTILINE))
    if [int(match[1]) for match in headings] != list(range(1, 15)):
        raise ValueError("queries.sql must contain numbered sections 1–14.")
    queries = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index < 13 else len(source)
        sql = source[heading.end() : end].strip()
        if index == 13:
            sql = re.sub(r"\s*COMMIT;\s*$", "", sql).strip()
        queries.append(Query(index + 1, heading[2], sql))
    return queries
