"""日终台账 / 账实核对差异。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantMixin, TimestampMixin


class DailyLedger(Base, TenantMixin, TimestampMixin):
    """日终台账快照：某店某日的权益变动汇总。"""
    __tablename__ = 'daily_ledgers'
    __table_args__ = (Index('ix_dledger_store_date', 'store_id', 'biz_date'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    biz_date: Mapped[str] = mapped_column(String(10), nullable=False)
    store_id: Mapped[str] = mapped_column(String(36), nullable=False)
    purchase_count: Mapped[int] = mapped_column(Integer, default=0)
    purchase_cents: Mapped[int] = mapped_column(Integer, default=0)
    consume_sessions: Mapped[int] = mapped_column(Integer, default=0)
    refund_count: Mapped[int] = mapped_column(Integer, default=0)
    refund_cents: Mapped[int] = mapped_column(Integer, default=0)
    active_entitlements: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default='GENERATED', nullable=False)


class ReconciliationDiff(Base, TenantMixin, TimestampMixin):
    """账实核对差异：挂起处理，需留痕。"""
    __tablename__ = 'reconciliation_diffs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    biz_date: Mapped[str] = mapped_column(String(10), nullable=False)
    store_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(String(240), default='')
    ref_id: Mapped[str] = mapped_column(String(36), default='')
    status: Mapped[str] = mapped_column(String(20), default='PENDING', nullable=False)  # PENDING/RESOLVED/IGNORED
    resolved_by: Mapped[str] = mapped_column(String(36), default='')
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_note: Mapped[str] = mapped_column(String(240), default='')
