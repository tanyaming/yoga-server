"""教练评价（FR-20）。先审后显：PENDING → PUBLISHED / HIDDEN，会员端只见已发布。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantMixin, TimestampMixin

REVIEW_STATUS = ('PENDING', 'PUBLISHED', 'HIDDEN')


class CoachReview(Base, TenantMixin, TimestampMixin):
    __tablename__ = 'coach_reviews'
    __table_args__ = (
        UniqueConstraint('session_id', 'member_id', name='uq_review_session_member'),
        Index('ix_review_coach_status', 'coach_id', 'status'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    coach_id: Mapped[str] = mapped_column(String(36), default='', nullable=False)
    rating: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    content: Mapped[str] = mapped_column(Text, default='', nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='PENDING', nullable=False)
    reject_reason: Mapped[str] = mapped_column(String(240), default='', nullable=False)
    moderated_by: Mapped[str] = mapped_column(String(36), default='', nullable=False)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
