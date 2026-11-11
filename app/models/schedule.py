"""排期 / 课程模板 / 约课（含候补）。约课五态在 enrollment.status。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantMixin, TimestampMixin

# 约课状态：BOOKED 已约 / WAITLIST 候补 / CANCELLED 已取消 / ATTENDED 已到课 / NO_SHOW 爽约
ENROLL_STATUS = ('BOOKED', 'WAITLIST', 'CANCELLED', 'ATTENDED', 'NO_SHOW')
# 课节状态：OPEN 可约 / FULL 已满 / CLOSED 已截止 / CANCELLED 已取消 / FINISHED 已结束
SESSION_STATUS = ('OPEN', 'FULL', 'CLOSED', 'CANCELLED', 'FINISHED')
# 课程类型：GROUP 团课 / PRIVATE 私教
COURSE_TYPES = ('GROUP', 'PRIVATE')


class CourseTemplate(Base, TenantMixin, TimestampMixin):
    """课程模板：团课/私教/普拉提等可重复排期的课。"""
    __tablename__ = 'course_templates'
    __table_args__ = (UniqueConstraint('tenant_id', 'code', name='uq_course_code'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    store_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    coach_id: Mapped[str] = mapped_column(String(36), default='')
    ctype: Mapped[str] = mapped_column(String(20), default='GROUP', nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='ACTIVE', nullable=False)


class ClassSession(Base, TenantMixin, TimestampMixin):
    """排期实例：某模板在具体时间的一场课。约课名额与状态在此。"""
    __tablename__ = 'class_sessions'
    __table_args__ = (Index('ix_session_store_time', 'store_id', 'starts_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(36), nullable=False)
    store_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    coach_id: Mapped[str] = mapped_column(String(36), default='')
    course_name: Mapped[str] = mapped_column(String(120), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    booked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='OPEN', nullable=False)
    cancel_deadline_hours: Mapped[int] = mapped_column(Integer, default=2, nullable=False)


class Enrollment(Base, TenantMixin, TimestampMixin):
    """约课记录：会员 × 课节唯一预约；候补并入本表以 waitlist_seq 排序。"""
    __tablename__ = 'enrollments'
    __table_args__ = (
        UniqueConstraint('session_id', 'member_id', name='uq_enroll_session_member'),
        Index('ix_enroll_member_time', 'member_id', 'created_at'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    store_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='BOOKED', nullable=False)
    source: Mapped[str] = mapped_column(String(20), default='MEMBER', nullable=False)  # MEMBER/COACH/ADMIN
    waitlist_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
