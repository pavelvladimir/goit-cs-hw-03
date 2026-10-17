"""Unit tests never inherit database credentials from the developer's shell."""

import os
from pathlib import Path

import pytest

from conf.settings import ROOT
from scripts.init_local import initialize


@pytest.fixture(autouse=True)
def clean_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in tuple(os.environ):
        if key.startswith(("PG", "MONGO", "DB_", "HW03_", "COMPOSE_")):
            monkeypatch.delenv(key)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("HW03_ENV_FILE", str(tmp_path / "absent.env"))


@pytest.fixture
def local_config(tmp_path: Path) -> Path:
    (tmp_path / ".env.example").write_bytes((ROOT / ".env.example").read_bytes())
    initialize(tmp_path)
    return tmp_path
