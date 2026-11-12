"""线下收款登记 / 退款。只登记金额，不接支付渠道。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantMixin, TimestampMixin

# 收款方式
PAY_METHODS = ('CASH', 'ALIPAY', 'WECHAT', 'CARD', 'OTHER')

# 收款单状态机：PAID 已收款 / PARTIAL_REFUNDED 部分退款 / REFUNDED 已全额退款 / VOID 已作废
# 营业收入只认 PAID + PARTIAL_REFUNDED + REFUNDED；VOID 视为「这笔钱没收到」。
PAYMENT_STATUS = ('PAID', 'PARTIAL_REFUNDED', 'REFUNDED', 'VOID')
# 计入营业收入（净额）的收款单状态
INCOME_PAYMENT_STATUS = ('PAID', 'PARTIAL_REFUNDED', 'REFUNDED')
# 退款单状态：DONE 已退款 / REVERSED 已红冲撤销
REFUND_STATUS = ('DONE', 'REVERSED')


class PaymentRecord(Base, TenantMixin, TimestampMixin):
    """线下收款登记：收款后生成权益 + 流水。

    - ``idempotency_key`` 唯一约束**含 tenant_id**：多租户复用同一键不会互相撞车；
    - ``refunded_cents`` 记录该收款单累计已退金额（分），
      收入净额 = ``amount_cents - refunded_cents``。
    """
    __tablename__ = 'payment_records'
    __table_args__ = (
        UniqueConstraint('tenant_id', 'idempotency_key', name='uq_payment_idem_tenant'),
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
    # 状态见 PAYMENT_STATUS：PAID/PARTIAL_REFUNDED/REFUNDED/VOID
    status: Mapped[str] = mapped_column(String(20), default='PAID', nullable=False)
    # 累计已退金额（分），恒 <= amount_cents，由退款服务与迁移回填共同保证
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    entitlement_id: Mapped[str] = mapped_column(String(36), default='')
    remark: Mapped[str] = mapped_column(String(240), default='')


class RefundRecord(Base, TenantMixin, TimestampMixin):
    """退款单：一条记录对应一次退款动作，可全额、可按课时、可按金额。

    ``idempotency_key`` 唯一约束同样含 tenant_id，重放同一请求返回原单而不重复扣钱。
    """
    __tablename__ = 'refund_records'
    __table_args__ = (
        UniqueConstraint('tenant_id', 'idempotency_key', name='uq_refund_idem_tenant'),
        Index('ix_refund_payment', 'payment_id'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_id: Mapped[str] = mapped_column(String(36), default='')
    entitlement_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False)
    store_id: Mapped[str] = mapped_column(String(36), default='')
    refund_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    # 课时型退款退掉的课时数；储值型为 0
    refund_sessions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    formula: Mapped[str] = mapped_column(String(240), default='')
    status: Mapped[str] = mapped_column(String(20), default='DONE', nullable=False)
    operator_id: Mapped[str] = mapped_column(String(36), default='')
    # 指向本次退款写入的 LedgerEntry.id（历史 bug 曾误写退款单自身 ID）
    ledger_entry_id: Mapped[str] = mapped_column(String(36), default='')
