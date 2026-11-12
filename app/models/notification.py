"""站内消息 / 订阅消息记录。

FR-21：统一通知通道的落库记录。``channel_status`` 明确标注微信订阅消息通道
是已送达 / 未配置资质跳过 / 发送失败，绝不把「站内信已写」伪装成「双通道已送达」。
"""
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
    scene: Mapped[str] = mapped_column(String(40), default='', nullable=False)
    channel_status: Mapped[str] = mapped_column(String(30), default='IN_APP_ONLY', nullable=False)
    channel_detail: Mapped[str] = mapped_column(String(240), default='', nullable=False)
