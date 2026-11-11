"""经营看板 / 账实核对服务。

看板指标：今日到课、今日消课、本月收入、活跃会员、到课率。
账实核对（无在线支付口径）：按日生成台账快照，检测「负余额」类数据异常并挂起处理，
防止线下收款不入系统造成的账外循环。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import NotFound
from ..models import (AttendanceRecord, DailyLedger, Entitlement, LedgerEntry, Member,
                      PaymentRecord, ReconciliationDiff, Store)
from ..repositories.base import write_audit
from ..security import random_id
from ..utils.money import cents_to_yuan
from ..utils.timeutil import business_date, now_naive, parse_dt


def _day_range(date: str):
    d = parse_dt(date)
    return d, d + timedelta(days=1)


def get_overview(db: Session, tenant_id: str, store_id: str = '') -> Dict:
    today = business_date()
    d0, d1 = _day_range(today)
    month_start = d0.replace(day=1)

    def _today_att(status: str) -> int:
        stmt = select(func.count(AttendanceRecord.id)).where(
            AttendanceRecord.tenant_id == tenant_id,
            AttendanceRecord.status == status,
            AttendanceRecord.created_at >= d0, AttendanceRecord.created_at < d1)
        if store_id:
            stmt = stmt.where(AttendanceRecord.store_id == store_id)
        return db.scalar(stmt) or 0

    attended = _today_att('ATTENDED')
    no_show = _today_att('NO_SHOW')

    pay_stmt = select(func.coalesce(func.sum(PaymentRecord.amount_cents), 0)).where(
        PaymentRecord.tenant_id == tenant_id, PaymentRecord.status == 'PAID',
        PaymentRecord.paid_at >= month_start)
    if store_id:
        pay_stmt = pay_stmt.where(PaymentRecord.store_id == store_id)
    month_income = db.scalar(pay_stmt) or 0

    active_stmt = select(func.count(Entitlement.id)).where(
        Entitlement.tenant_id == tenant_id, Entitlement.status == 'ACTIVE')
    if store_id:
        # 权益不绑店，跨店通用；店铺维度活跃会员按「有本店约课」近似
        active_stmt = active_stmt.where(Entitlement.member_id.in_(
            select(AttendanceRecord.member_id).where(
                AttendanceRecord.tenant_id == tenant_id, AttendanceRecord.store_id == store_id)))
    active_members = db.scalar(active_stmt) or 0

    att_rate = round(attended / (attended + no_show) * 100, 1) if (attended + no_show) else 0.0

    pending_diffs = db.scalar(select(func.count(ReconciliationDiff.id)).where(
        ReconciliationDiff.tenant_id == tenant_id,
        ReconciliationDiff.status == 'PENDING')) or 0

    return {
        'today_attended': attended, 'today_no_show': no_show,
        'today_consume': attended,  # 团课每节=1 人次消课
        'month_income_cents': month_income, 'month_income': cents_to_yuan(month_income),
        'active_members': active_members, 'attendance_rate': att_rate,
        'pending_diffs': pending_diffs,
    }


def get_trend(db: Session, tenant_id: str, store_id: str = '', days: int = 7) -> List[Dict]:
    out = []
    for i in range(days - 1, -1, -1):
        d = now_naive().date() - timedelta(days=i)
        d0 = parse_dt(d.strftime('%Y-%m-%d'))
        d1 = d0 + timedelta(days=1)
        stmt = select(func.count(AttendanceRecord.id)).where(
            AttendanceRecord.tenant_id == tenant_id,
            AttendanceRecord.status == 'ATTENDED',
            AttendanceRecord.created_at >= d0, AttendanceRecord.created_at < d1)
        if store_id:
            stmt = stmt.where(AttendanceRecord.store_id == store_id)
        out.append({'date': d.strftime('%m-%d'), 'attended': db.scalar(stmt) or 0})
    return out


def get_store_comparison(db: Session, tenant_id: str) -> List[Dict]:
    stores = db.scalars(select(Store).where(Store.tenant_id == tenant_id)).all()
    out = []
    for st in stores:
        income = db.scalar(select(func.coalesce(func.sum(PaymentRecord.amount_cents), 0)).where(
            PaymentRecord.tenant_id == tenant_id, PaymentRecord.status == 'PAID',
            PaymentRecord.store_id == st.id)) or 0
        consume = db.scalar(select(func.count(AttendanceRecord.id)).where(
            AttendanceRecord.tenant_id == tenant_id, AttendanceRecord.status == 'ATTENDED',
            AttendanceRecord.store_id == st.id)) or 0
        out.append({
            'store_id': st.id, 'name': st.name,
            'income_cents': income, 'consume_sessions': consume,
        })
    return out


def get_renewal_list(db: Session, tenant_id: str) -> List[Dict]:
    """待续卡：权益 ACTIVE 且剩余课时 ≤2，或 30 天内到期。"""
    soon = now_naive() + timedelta(days=30)
    ents = db.scalars(select(Entitlement).where(
        Entitlement.tenant_id == tenant_id, Entitlement.status == 'ACTIVE')).all()
    rows = []
    seen = set()
    for e in ents:
        remain = e.total_sessions - e.used_sessions
        due_soon = e.expires_at is not None and e.expires_at <= soon
        if remain <= 2 or due_soon:
            if e.member_id in seen:
                continue
            seen.add(e.member_id)
            m = db.get(Member, e.member_id)
            rows.append({
                'member_id': e.member_id, 'name': m.name if m else '',
                'phone_mask': m.phone_mask if m else '',
                'remain_sessions': remain,
                'expires_at': e.expires_at.strftime('%Y-%m-%d') if e.expires_at else '',
            })
    return rows


# ---------- 账实核对 ----------
def reconcile_daily(db: Session, tenant_id: str, store_id: str, date: str) -> Dict:
    d0, d1 = _day_range(date)

    def _sum_pay():
        stmt = select(func.count(PaymentRecord.id),
                      func.coalesce(func.sum(PaymentRecord.amount_cents), 0)).where(
            PaymentRecord.tenant_id == tenant_id, PaymentRecord.status == 'PAID',
            PaymentRecord.paid_at >= d0, PaymentRecord.paid_at < d1)
        if store_id:
            stmt = stmt.where(PaymentRecord.store_id == store_id)
        cnt, sm = db.execute(stmt).one()
        return cnt or 0, sm or 0

    def _sum_consume():
        stmt = select(func.count(AttendanceRecord.id)).where(
            AttendanceRecord.tenant_id == tenant_id,
            AttendanceRecord.status == 'ATTENDED',
            AttendanceRecord.created_at >= d0, AttendanceRecord.created_at < d1)
        if store_id:
            stmt = stmt.where(AttendanceRecord.store_id == store_id)
        return db.scalar(stmt) or 0

    pay_cnt, pay_cents = _sum_pay()
    consume = _sum_consume()

    # 台账快照（同店同日幂等：先删旧再生成，保证重跑一致）
    old = db.scalars(select(DailyLedger).where(
        DailyLedger.tenant_id == tenant_id, DailyLedger.store_id == store_id,
        DailyLedger.biz_date == date)).all()
    for o in old:
        db.delete(o)
    db.add(DailyLedger(id=random_id(), tenant_id=tenant_id, biz_date=date, store_id=store_id,
                       purchase_count=pay_cnt, purchase_cents=pay_cents,
                       consume_sessions=consume, status='GENERATED'))

    # 差异检测：负余额会员（累计消耗 > 累计购买课时）
    diffs = _detect_negative_balance(db, tenant_id, store_id, date)
    return {
        'date': date, 'purchase_count': pay_cnt, 'purchase_cents': pay_cents,
        'consume_sessions': consume, 'new_diffs': len(diffs),
    }


def _detect_negative_balance(db: Session, tenant_id: str, store_id: str, date: str) -> List[str]:
    """检测累计课时为负的会员（数据异常 → 挂起）。"""
    members = db.scalars(select(Member.id).where(Member.tenant_id == tenant_id)).all()
    found = []
    for mid in members:
        bought = db.scalar(select(func.coalesce(func.sum(LedgerEntry.delta_sessions), 0)).where(
            LedgerEntry.tenant_id == tenant_id, LedgerEntry.member_id == mid,
            LedgerEntry.delta_sessions > 0)) or 0
        consumed = db.scalar(select(func.coalesce(func.sum(-LedgerEntry.delta_sessions), 0)).where(
            LedgerEntry.tenant_id == tenant_id, LedgerEntry.member_id == mid,
            LedgerEntry.delta_sessions < 0)) or 0
        if consumed > bought:
            dup = db.scalar(select(ReconciliationDiff).where(
                ReconciliationDiff.tenant_id == tenant_id, ReconciliationDiff.ref_id == mid,
                ReconciliationDiff.status == 'PENDING'))
            if not dup:
                db.add(ReconciliationDiff(
                    id=random_id(), tenant_id=tenant_id, biz_date=date, store_id=store_id,
                    kind='NEGATIVE_BALANCE', ref_id=mid,
                    description='会员 %s 累计消课 %d 节 > 购买 %d 节' % (mid[:8], consumed, bought)))
                found.append(mid)
    return found


def list_diffs(db: Session, tenant_id: str, status: str = 'PENDING') -> List[Dict]:
    stmt = select(ReconciliationDiff).where(ReconciliationDiff.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(ReconciliationDiff.status == status)
    rows = db.scalars(stmt.order_by(ReconciliationDiff.created_at.desc()).limit(100)).all()
    return [{
        'id': d.id, 'biz_date': d.biz_date, 'store_id': d.store_id, 'kind': d.kind,
        'description': d.description, 'status': d.status, 'resolution_note': d.resolution_note,
    } for d in rows]


def resolve_diff(db: Session, tenant_id: str, operator: Dict, diff_id: str, note: str) -> Dict:
    d = db.get(ReconciliationDiff, diff_id)
    if not d or d.tenant_id != tenant_id:
        raise NotFound('差异记录不存在')
    d.status = 'RESOLVED'
    d.resolved_by = operator['id']
    d.resolved_at = now_naive()
    d.resolution_note = note or ''
    write_audit(db, tenant_id, operator['id'], operator['name'], 'RESOLVE', 'reconciliation_diff',
                d.id, {'note': note})
    return {'id': d.id, 'status': d.status}
