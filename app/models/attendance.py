"""点名消课：幂等唯一约束 member+session，重复点名不重复扣。"""
from __future__ import annotations

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantMixin, TimestampMixin


class AttendanceRecord(Base, TenantMixin, TimestampMixin):
    __tablename__ = 'attendance_records'
    __table_args__ = (
        UniqueConstraint('session_id', 'member_id', name='uq_attendance_session_member'),
        Index('ix_attendance_coach_time', 'coach_id', 'created_at'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    enrollment_id: Mapped[str] = mapped_column(String(36), default='')
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    coach_id: Mapped[str] = mapped_column(String(36), default='')
    store_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sessions_consumed: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='ATTENDED', nullable=False)  # ATTENDED/NO_SHOW
    ledger_entry_id: Mapped[str] = mapped_column(String(36), default='')
