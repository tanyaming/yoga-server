"""线下收款登记 / 退款。只登记金额，不接支付渠道。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantMixin, TimestampMixin

# 收款方式
PAY_METHODS = ('CASH', 'ALIPAY', 'WECHAT', 'CARD', 'OTHER')


class PaymentRecord(Base, TenantMixin, TimestampMixin):
    """线下收款登记：收款后生成权益 + 流水。幂等键防止重复入账。"""
    __tablename__ = 'payment_records'
    __table_args__ = (
        UniqueConstraint('idempotency_key', name='uq_payment_idem'),
        Index('ix_payment_store_date', 'store_id', 'paid_at'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_no: Mapped[str] = mapped_column(String(40), default='')
    store_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_name: Mapped[str] = mapped_column(String(120), default='')
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    method: Mapped[str] = mapped_column(String(20), default='CASH', nullable=False)
    operator_id: Mapped[str] = mapped_column(String(36), default='')
    status: Mapped[str] = mapped_column(String(20), default='PAID', nullable=False)  # PAID/VOID
    entitlement_id: Mapped[str] = mapped_column(String(36), default='')
    remark: Mapped[str] = mapped_column(String(240), default='')


class RefundRecord(Base, TenantMixin, TimestampMixin):
    __tablename__ = 'refund_records'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payment_id: Mapped[str] = mapped_column(String(36), default='')
    entitlement_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False)
    refund_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    formula: Mapped[str] = mapped_column(String(240), default='')
    operator_id: Mapped[str] = mapped_column(String(36), default='')
    ledger_entry_id: Mapped[str] = mapped_column(String(36), default='')
