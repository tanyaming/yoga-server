"""站内消息 / 订阅消息记录。"""
from __future__ import annotations

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantMixin, TimestampMixin


class InAppMessage(Base, TenantMixin, TimestampMixin):
    __tablename__ = 'in_app_messages'
    __table_args__ = (Index('ix_msg_member_read', 'member_id', 'is_read'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(120), default='')
    content: Mapped[str] = mapped_column(Text, default='')
    mtype: Mapped[str] = mapped_column(String(20), default='SYSTEM', nullable=False)  # SYSTEM/BOOKING/ENTITLEMENT
    ref_id: Mapped[str] = mapped_column(String(36), default='')
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
