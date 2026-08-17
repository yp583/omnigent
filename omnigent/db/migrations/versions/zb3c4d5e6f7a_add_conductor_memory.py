"""Add per-user Conductors and provider-neutral Markdown memory manifests.

Revision ID: zb3c4d5e6f7a
Revises: za2b3c4d5e6f
Create Date: 2026-08-16 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import _CKSUM32, Uuid16

revision: str = "zb3c4d5e6f7a"
down_revision: str | None = "za2b3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create Conductor identity, memory manifest, and revision tables."""
    op.create_table(
        "conductors",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("conversation_id", Uuid16(), nullable=False),
        sa.Column("memory_provider", sa.String(64), nullable=False, server_default="markdown"),
        sa.Column("config", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )
    op.create_index(
        "ux_conductors_conversation_id",
        "conductors",
        ["workspace_id", "conversation_id"],
        unique=True,
    )
    op.create_table(
        "memory_documents",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("path_hash", _CKSUM32, nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("artifact_key", sa.String(1024), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", "path_hash"),
    )
    op.create_table(
        "memory_revisions",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("path_hash", _CKSUM32, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("artifact_key", sa.String(1024), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", "path_hash", "revision"),
    )


def downgrade() -> None:
    """Drop Conductor memory tables and identity binding."""
    op.drop_table("memory_revisions")
    op.drop_table("memory_documents")
    op.drop_index("ux_conductors_conversation_id", table_name="conductors")
    op.drop_table("conductors")
