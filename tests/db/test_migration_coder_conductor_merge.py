"""Tests for the Coder and Conductor migration-head merge."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from omnigent.db.utils import _build_alembic_config

_CODER_REVISION = "zb1c2d3e4f5a"
_CONDUCTOR_REVISION = "zb3c4d5e6f7a"
_MERGE_REVISION = "zb4c5d6e7f8a"


def test_migration_graph_has_one_head_joining_both_feature_branches() -> None:
    """The repository exposes one head whose parents are both feature tips."""
    config = _build_alembic_config("sqlite://")
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == [_MERGE_REVISION]
    merge_revision = scripts.get_revision(_MERGE_REVISION)
    assert merge_revision is not None
    assert set(merge_revision.down_revision) == {_CODER_REVISION, _CONDUCTOR_REVISION}


def test_live_conductor_revision_upgrades_without_losing_memory(tmp_path: Path) -> None:
    """A DB at the live Conductor head gains Coder identity and keeps its rows."""
    uri = f"sqlite:///{tmp_path / 'conductor-live.db'}"
    config = _build_alembic_config(uri)
    engine = create_engine(uri)
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, _CONDUCTOR_REVISION)
            connection.execute(
                sa.text(
                    """
                    INSERT INTO conductors (
                        workspace_id, user_id, conversation_id, memory_provider,
                        created_at
                    ) VALUES (
                        0, 'live-user', :conversation_id, 'markdown', 1
                    )
                    """
                ),
                {"conversation_id": bytes.fromhex("00112233445566778899aabbccddeeff")},
            )

        assert "coder_workspace_id" not in {
            column["name"] for column in sa.inspect(engine).get_columns("hosts")
        }

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

        assert "coder_workspace_id" in {
            column["name"] for column in sa.inspect(engine).get_columns("hosts")
        }
        with engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT user_id, memory_provider FROM conductors WHERE workspace_id = 0")
            ).one() == ("live-user", "markdown")
            current_revision = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert current_revision == _MERGE_REVISION
    finally:
        engine.dispose()
