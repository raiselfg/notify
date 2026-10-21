from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PushEvent(Base):
    __tablename__ = "push_events"
    __table_args__ = (
        UniqueConstraint("task_id", "scheduled_at", "schedule_version"),
        CheckConstraint("expires_at > created_at", name="positive_lifetime"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    schedule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    no_subscriptions: Mapped[bool] = mapped_column(Boolean, nullable=False)


class PushDelivery(Base):
    __tablename__ = "push_deliveries"
    __table_args__ = (
        UniqueConstraint("event_id", "subscription_id"),
        CheckConstraint("attempt_count >= 0", name="non_negative_attempt_count"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'accepted', 'failed', "
            "'expired', 'cancelled', 'invalid_subscription')",
            name="valid_status",
        ),
        CheckConstraint(
            "(status = 'processing' AND lease_token IS NOT NULL "
            "AND lease_until IS NOT NULL) OR "
            "(status <> 'processing' AND lease_token IS NULL AND lease_until IS NULL)",
            name="valid_lease",
        ),
        Index("ix_push_deliveries_ready", "status", "available_at"),
        Index("ix_push_deliveries_recovery", "status", "lease_until"),
        Index("ix_push_deliveries_subscription_id", "subscription_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("push_events.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("push_subscriptions.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column()
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
