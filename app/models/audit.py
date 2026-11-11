"""审计日志：关键写操作全留痕，越权零容忍。"""
from __future__ import annotations

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantMixin, TimestampMixin


class AuditLog(Base, TenantMixin, TimestampMixin):
    __tablename__ = 'audit_logs'
    __table_args__ = (Index('ix_audit_actor_time', 'actor_id', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(36), default='')
    actor_name: Mapped[str] = mapped_column(String(60), default='')
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    resource: Mapped[str] = mapped_column(String(40), default='')
    resource_id: Mapped[str] = mapped_column(String(36), default='')
    detail: Mapped[str] = mapped_column(Text, default='')
    ip: Mapped[str] = mapped_column(String(48), default='')
