"""模型基类：所有业务表强制 tenant_id，Repository 层收口租户校验。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..utils.timeutil import now_naive


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_naive,
                                                 onupdate=now_naive, nullable=False)


class TenantMixin:
    """平台级记录的 tenant_id 用 '' 表示；业务数据一律为真实租户 ID。"""
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)


class SoftDeleteMixin:
    status: Mapped[str] = mapped_column(String(20), default='ACTIVE', nullable=False)
