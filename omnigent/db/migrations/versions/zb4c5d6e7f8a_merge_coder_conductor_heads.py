"""Merge the Coder host identity and Conductor memory branches.

Revision ID: zb4c5d6e7f8a
Revises: zb1c2d3e4f5a, zb3c4d5e6f7a
Create Date: 2026-08-19 00:00:00.000000

Both parent revisions independently extend ``za2b3c4d5e6f``. This no-op merge
keeps both schemas and restores a single Alembic head. In particular, a live
database already at the Conductor revision can upgrade by applying the Coder
host column and then this marker without replaying Conductor DDL.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "zb4c5d6e7f8a"
down_revision: tuple[str, str] = ("zb1c2d3e4f5a", "zb3c4d5e6f7a")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join the two schema branches without additional DDL."""


def downgrade() -> None:
    """Split the migration graph back to its two parent revisions."""
