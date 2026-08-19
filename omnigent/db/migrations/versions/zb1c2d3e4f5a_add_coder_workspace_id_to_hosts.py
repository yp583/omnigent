"""add Coder workspace identity to hosts

Revision ID: zb1c2d3e4f5a
Revises: za2b3c4d5e6f
Create Date: 2026-08-19 00:00:00.000000

Persists the immutable Coder workspace UUID advertised by an external
``omnigent host``. The identifier lets dispatch intersect Coder's workspace
API with connected Omni hosts without copying CPU or memory telemetry into
Omni. ``NULL`` keeps every non-Coder host unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zb1c2d3e4f5a"
down_revision: str | None = "za2b3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable Coder identity and its per-owner uniqueness guard."""
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.add_column(sa.Column("coder_workspace_id", sa.String(64), nullable=True))
        batch_op.create_unique_constraint(
            "uq_hosts_workspace_user_coder_workspace",
            ["workspace_id", "user_id", "coder_workspace_id"],
        )


def downgrade() -> None:
    """Remove the Coder identity column and uniqueness guard."""
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.drop_constraint(
            "uq_hosts_workspace_user_coder_workspace",
            type_="unique",
        )
        batch_op.drop_column("coder_workspace_id")
