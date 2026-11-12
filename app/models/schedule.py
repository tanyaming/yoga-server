"""排期 / 课程模板 / 约课（含候补）。约课状态在 enrollment.status。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantMixin, TimestampMixin

# 约课状态：BOOKED 已约 / WAITLIST 候补 / OFFERED 递补待确认（FR-14 锁定窗口）
#           / CANCELLED 已取消 / ATTENDED 已到课 / NO_SHOW 爽约
ENROLL_STATUS = ('BOOKED', 'WAITLIST', 'OFFERED', 'CANCELLED', 'ATTENDED', 'NO_SHOW')
# 课节状态：OPEN 可约 / FULL 已满 / CLOSED 已截止 / CANCELLED 已取消 / FINISHED 已结束
SESSION_STATUS = ('OPEN', 'FULL', 'CLOSED', 'CANCELLED', 'FINISHED')
# 课程类型：GROUP 团课 / PRIVATE 私教
COURSE_TYPES = ('GROUP', 'PRIVATE')


class CourseTemplate(Base, TenantMixin, TimestampMixin):
    """课程模板：团课/私教/普拉提等可重复排期的课。

    FR-11 / FR-13：``allowed_ptypes`` 为可约的商品类型（PACKAGE/PRIVATE/STORED），
    逗号分隔，空串=不限；服务端据此做卡种匹配的最终校验。
    """
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
    allowed_ptypes: Mapped[str] = mapped_column(String(80), default='', nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='ACTIVE', nullable=False)


class ClassSession(Base, TenantMixin, TimestampMixin):
    """排期实例：某模板在具体时间的一场课。约课名额与状态在此。

    ``booked`` 是「已占位人数」（BOOKED + OFFERED），由单语句条件 UPDATE 与
    ``recount_session_booked`` 维护；库级 CHECK 兜底，任何绕过服务的写入都无法造出
    超卖或负数名额（MySQL 8.0.16+ 才强制 CHECK，SQLite 全程强制）。
    """
    __tablename__ = 'class_sessions'
    __table_args__ = (
        Index('ix_session_store_time', 'store_id', 'starts_at'),
        CheckConstraint('booked >= 0 AND booked <= capacity', name='ck_session_booked_range'),
    )
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
    """约课记录：会员 × 课节唯一预约；候补并入本表以 waitlist_seq 排序。

    FR-14：候补递补先进入 OFFERED 并锁定 15 分钟（offer_expires_at），
    会员确认后才转 BOOKED；超时未确认自动顺延下一位。
    """
    __tablename__ = 'enrollments'
    __table_args__ = (
        UniqueConstraint('session_id', 'member_id', name='uq_enroll_session_member'),
        Index('ix_enroll_member_time', 'member_id', 'created_at'),
        # 名额重算与点名名单都按 (session_id, status) 过滤
        Index('ix_enroll_session_status', 'session_id', 'status'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    store_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='BOOKED', nullable=False)
    source: Mapped[str] = mapped_column(String(20), default='MEMBER', nullable=False)  # MEMBER/COACH/ADMIN
    waitlist_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    offer_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
