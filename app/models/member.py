"""会员：租户内以手机号哈希唯一，跨店通用；跟进备注会员不可见。"""
from __future__ import annotations

from sqlalchemy import Boolean, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantMixin, TimestampMixin


class Member(Base, TenantMixin, TimestampMixin):
    __tablename__ = 'members'
    __table_args__ = (UniqueConstraint('tenant_id', 'phone_hash', name='uq_member_phone'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_mask: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    openid: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source_store_id: Mapped[str] = mapped_column(String(36), default='')
    status: Mapped[str] = mapped_column(String(20), default='ACTIVE', nullable=False)
    note: Mapped[str] = mapped_column(Text, default='')


class MemberTag(Base, TenantMixin, TimestampMixin):
    __tablename__ = 'member_tags'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    tag: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), default='')


class FollowUp(Base, TenantMixin, TimestampMixin):
    __tablename__ = 'follow_ups'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    coach_id: Mapped[str] = mapped_column(String(36), default='')
    content: Mapped[str] = mapped_column(Text, default='')
    visible_to_member: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), default='')
