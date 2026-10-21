"""Create persistent push queue.

Revision ID: b74c91e2a6d0
Revises: 98f2e6a31c07
"""

from alembic import op
import sqlalchemy as sa

revision = "b74c91e2a6d0"
down_revision = "98f2e6a31c07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("schedule_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_table(
        "push_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("no_subscriptions", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_push_events_positive_lifetime")
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="CASCADE",
            name=op.f("fk_push_events_task_id_tasks"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_events")),
        sa.UniqueConstraint(
            "task_id",
            "scheduled_at",
            "schedule_version",
            name=op.f("uq_push_events_task_id"),
        ),
    )
    op.create_table(
        "push_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_reason", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_push_deliveries_non_negative_attempt_count"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'accepted', 'failed', "
            "'expired', 'cancelled', 'invalid_subscription')",
            name=op.f("ck_push_deliveries_valid_status"),
        ),
        sa.CheckConstraint(
            "(status = 'processing' AND lease_token IS NOT NULL "
            "AND lease_until IS NOT NULL) OR "
            "(status <> 'processing' AND lease_token IS NULL AND lease_until IS NULL)",
            name=op.f("ck_push_deliveries_valid_lease"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["push_events.id"],
            ondelete="CASCADE",
            name=op.f("fk_push_deliveries_event_id_push_events"),
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["push_subscriptions.id"],
            ondelete="SET NULL",
            name=op.f("fk_push_deliveries_subscription_id_push_subscriptions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_deliveries")),
        sa.UniqueConstraint(
            "event_id", "subscription_id", name=op.f("uq_push_deliveries_event_id")
        ),
    )
    op.create_index(
        "ix_push_deliveries_ready", "push_deliveries", ["status", "available_at"]
    )
    op.create_index(
        "ix_push_deliveries_recovery", "push_deliveries", ["status", "lease_until"]
    )
    op.create_index(
        "ix_push_deliveries_subscription_id", "push_deliveries", ["subscription_id"]
    )


def downgrade() -> None:
    op.drop_table("push_deliveries")
    op.drop_table("push_events")
    op.drop_column("tasks", "schedule_version")
