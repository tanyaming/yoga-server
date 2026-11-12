"""商品 / 权益实例 / 权益流水。账实一致底线的数据底座。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantMixin, TimestampMixin

# 商品类型：PACKAGE 课程包 / PRIVATE 私教 / STORED 储值
PRODUCT_TYPES = ('PACKAGE', 'PRIVATE', 'STORED')
# 权益状态
ENTITLEMENT_STATUS = ('ACTIVE', 'EXPIRED', 'DEPLETED', 'REFUNDED')
# 流水类型
LEDGER_KINDS = ('PURCHASE', 'CONSUME', 'REFUND', 'ADJUST', 'EXPIRE')


class Product(Base, TenantMixin, TimestampMixin):
    """商品定义：课程包 / 私教 / 储值卡，价格与课时口径。

    FR-07 / FR-16 适用规则：
    - ``applicable_store_ids``：适用门店 ID，逗号分隔，空串=不限门店；
    - ``cross_store``：True 表示租户内跨店通用，此时忽略 applicable_store_ids；
    - ``allowed_ctypes``：可约课型（GROUP/PRIVATE），逗号分隔，空串=不限。
    """
    __tablename__ = 'products'
    __table_args__ = (UniqueConstraint('tenant_id', 'code', name='uq_product_code'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    ptype: Mapped[str] = mapped_column(String(20), nullable=False)  # PACKAGE/PRIVATE/STORED
    total_sessions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_days: Mapped[int] = mapped_column(Integer, default=365, nullable=False)
    cross_store: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    applicable_store_ids: Mapped[str] = mapped_column(String(240), default='', nullable=False)
    allowed_ctypes: Mapped[str] = mapped_column(String(80), default='', nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='ACTIVE', nullable=False)
    description: Mapped[str] = mapped_column(Text, default='')


class Entitlement(Base, TenantMixin, TimestampMixin):
    """权益实例：某会员持有的一笔可消耗额度（课时或余额）。

    FR-16：适用规则在收款时从商品**快照**下来，商品后续配置变更不影响已售权益。
    空串 / False 表示不限制，保证历史数据与旧客户端兼容。
    """
    __tablename__ = 'entitlements'
    __table_args__ = (Index('ix_ent_member_status', 'member_id', 'status'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_name: Mapped[str] = mapped_column(String(120), default='')
    ptype: Mapped[str] = mapped_column(String(20), nullable=False)
    total_sessions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    used_sessions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    balance_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='ACTIVE', nullable=False)
    source_payment_id: Mapped[str] = mapped_column(String(36), default='')
    cross_store: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    applicable_store_ids: Mapped[str] = mapped_column(String(240), default='', nullable=False)
    allowed_ctypes: Mapped[str] = mapped_column(String(80), default='', nullable=False)


class LedgerEntry(Base, TenantMixin, TimestampMixin):
    """权益流水：只追加不修改，带操作人留痕，可完整重放账本。"""
    __tablename__ = 'ledger_entries'
    __table_args__ = (
        UniqueConstraint('idempotency_key', name='uq_ledger_idem'),
        Index('ix_ledger_member_time', 'member_id', 'created_at'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    entitlement_id: Mapped[str] = mapped_column(String(36), default='')
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # PURCHASE/CONSUME/REFUND/ADJUST/EXPIRE
    delta_sessions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delta_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    balance_sessions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    balance_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ref_type: Mapped[str] = mapped_column(String(40), default='')
    ref_id: Mapped[str] = mapped_column(String(36), default='')
    remark: Mapped[str] = mapped_column(String(240), default='')
    operator_id: Mapped[str] = mapped_column(String(36), default='')
