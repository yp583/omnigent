"""Tests for the hosts Coder workspace identity migration."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from omnigent.db.utils import clear_engine_cache, get_or_create_engine


@pytest.fixture
def db_engine(tmp_path: Path) -> Iterator[Engine]:
    """Create a fresh SQLite database migrated to the current head."""
    uri = f"sqlite:///{tmp_path / 'coder-host.db'}"
    engine = get_or_create_engine(uri)
    try:
        yield engine
    finally:
        clear_engine_cache()


def test_hosts_have_nullable_coder_workspace_identity(db_engine: Engine) -> None:
    """The migrated hosts table exposes the optional Coder UUID column."""
    columns = {column["name"]: column for column in sa.inspect(db_engine).get_columns("hosts")}
    assert columns["coder_workspace_id"]["nullable"] is True


def test_coder_workspace_identity_is_unique_per_owner(db_engine: Engine) -> None:
    """One Coder workspace maps to at most one Omni host for an owner."""
    constraints = sa.inspect(db_engine).get_unique_constraints("hosts")
    constraint = next(
        item for item in constraints if item["name"] == "uq_hosts_workspace_user_coder_workspace"
    )
    assert constraint["column_names"] == [
        "workspace_id",
        "user_id",
        "coder_workspace_id",
    ]
