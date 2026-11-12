"""调课 / 停课申请单（FR-12）。申请 → 审批 → 变更排期 → 通知已约会员。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantMixin, TimestampMixin

CHANGE_TYPES = ('RESCHEDULE', 'COACH_CHANGE', 'CANCEL')
CHANGE_STATUS = ('PENDING', 'APPROVED', 'REJECTED')


class ScheduleChangeRequest(Base, TenantMixin, TimestampMixin):
    __tablename__ = 'schedule_change_requests'
    __table_args__ = (Index('ix_change_session_status', 'session_id', 'status'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    requester_id: Mapped[str] = mapped_column(String(36), default='', nullable=False)
    requester_name: Mapped[str] = mapped_column(String(60), default='', nullable=False)
    change_type: Mapped[str] = mapped_column(String(20), default='RESCHEDULE', nullable=False)
    new_starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    new_coach_id: Mapped[str] = mapped_column(String(36), default='', nullable=False)
    reason: Mapped[str] = mapped_column(Text, default='', nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='PENDING', nullable=False)
    approver_id: Mapped[str] = mapped_column(String(36), default='', nullable=False)
    review_note: Mapped[str] = mapped_column(String(240), default='', nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
