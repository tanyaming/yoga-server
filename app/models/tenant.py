"""租户 / 门店 / 账号 / 刷新令牌。租户=品牌，门店=经营场所。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, TenantMixin

ROLES = ('PLATFORM_ADMIN', 'TENANT_ADMIN', 'STORE_MANAGER', 'COACH')


class Tenant(Base, TimestampMixin):
    __tablename__ = 'tenants'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='ACTIVE', nullable=False)
    contact_name: Mapped[str] = mapped_column(String(60), default='')
    contact_phone_mask: Mapped[str] = mapped_column(String(20), default='')
    plan: Mapped[str] = mapped_column(String(30), default='STANDARD')


class Store(Base, TenantMixin, TimestampMixin):
    __tablename__ = 'stores'
    __table_args__ = (UniqueConstraint('tenant_id', 'code', name='uq_store_code'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str] = mapped_column(String(240), default='')
    phone_mask: Mapped[str] = mapped_column(String(20), default='')
    business_hours: Mapped[str] = mapped_column(String(120), default='09:00-21:00')
    status: Mapped[str] = mapped_column(String(20), default='ACTIVE', nullable=False)


class User(Base, TenantMixin, TimestampMixin):
    """后台账号与教练账号；会员不是 User，Member 单独建模。"""
    __tablename__ = 'users'
    __table_args__ = (
        UniqueConstraint('tenant_id', 'username', name='uq_user_username'),
        UniqueConstraint('tenant_id', 'openid', name='uq_user_openid'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(60), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), default='')
    real_name: Mapped[str] = mapped_column(String(60), default='')
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    store_id: Mapped[str] = mapped_column(String(36), default='')
    openid: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='ACTIVE', nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RefreshToken(Base, TenantMixin, TimestampMixin):
    """服务端存储哈希，明文只在登录响应中出现一次。"""
    __tablename__ = 'refresh_tokens'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
