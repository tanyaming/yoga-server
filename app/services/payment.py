"""收款登记 / 权益账本 / 退款：账实一致底线的核心服务。

口径（PRD 已确认）：无在线支付，线下收款、线上记账。
- 收款登记幂等：幂等键重复且参数一致 → 返回原单；参数不一致 → 冲突。
- 收款即生成权益实例 + 权益流水（PURCHASE），金额一律「分」存储。
- 退款按「售价 ÷ 总课时」比例折算，折算过程写入 formula 字段可复核。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import (BusinessRuleError, Conflict, IdempotencyConflict, NotFound,
                      ValidationError)
from ..models import (Entitlement, LedgerEntry, Member, PaymentRecord, Product,
                      RefundRecord, User)
from ..repositories.base import write_audit
from ..security import random_id
from ..utils.money import refund_amount_cents, refund_formula_text
from ..utils.timeutil import now_naive, parse_dt

_PAY_METHODS = ('CASH', 'ALIPAY', 'WECHAT', 'CARD', 'OTHER')


# ---------- 商品 ----------
def create_product(db: Session, tenant_id: str, operator: Dict, data: dict) -> Product:
    ptype = data.get('ptype') or 'PACKAGE'
    if ptype not in ('PACKAGE', 'PRIVATE', 'STORED'):
        raise ValidationError('商品类型非法')
    name = (data.get('name') or '').strip()
    if not name:
        raise ValidationError('商品名称不能为空')
    code = data.get('code') or ('P' + random_id()[:6].upper())
    exists = db.scalar(select(Product).where(Product.tenant_id == tenant_id, Product.code == code))
    if exists:
        raise Conflict('商品编码已存在')
    total_sessions = int(data.get('total_sessions', 0) or 0)
    price_cents = int(data.get('price_cents', 0) or 0)
    if ptype in ('PACKAGE', 'PRIVATE') and total_sessions <= 0:
        raise ValidationError('课时型商品总课时必须 > 0')
    if ptype == 'STORED':
        total_sessions = 0
    if price_cents < 0:
        raise ValidationError('价格不能为负')
    p = Product(id=random_id(), tenant_id=tenant_id, code=code, name=name, ptype=ptype,
                total_sessions=total_sessions, price_cents=price_cents,
                valid_days=int(data.get('valid_days', 365) or 365),
                cross_store=bool(data.get('cross_store', False)),
                description=data.get('description', ''))
    db.add(p)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'CREATE', 'product', p.id,
                {'name': name, 'ptype': ptype})
    return p


def list_products(db: Session, tenant_id: str) -> List[Product]:
    return list(db.scalars(select(Product).where(
        Product.tenant_id == tenant_id, Product.status == 'ACTIVE')
        .order_by(Product.created_at.desc())).all())


# ---------- 收款登记 ----------
def register_payment(db: Session, tenant_id: str, operator: Dict, data: dict) -> Dict:
    member_id = data.get('member_id', '')
    product_id = data.get('product_id', '')
    store_id = data.get('store_id', '')
    amount_cents = int(data.get('amount_cents', 0) or 0)
    method = data.get('method') or 'CASH'
    idem_key = (data.get('idempotency_key') or '').strip()

    if method not in _PAY_METHODS:
        raise ValidationError('收款方式非法')

    # 幂等：同键同参数返回原单
    if idem_key:
        existing = db.scalar(select(PaymentRecord).where(
            PaymentRecord.idempotency_key == idem_key,
            PaymentRecord.tenant_id == tenant_id))
        if existing:
            if (existing.member_id != member_id or existing.product_id != product_id
                    or existing.amount_cents != amount_cents):
                raise IdempotencyConflict()
            return _payment_payload(existing)

    member = db.get(Member, member_id)
    if not member or member.tenant_id != tenant_id:
        raise NotFound('会员不存在')
    product = db.get(Product, product_id)
    if not product or product.tenant_id != tenant_id:
        raise NotFound('商品不存在')
    if product.status != 'ACTIVE':
        raise BusinessRuleError('商品已下架')
    if amount_cents <= 0:
        raise ValidationError('收款金额必须大于 0')
    if amount_cents != product.price_cents:
        raise BusinessRuleError('收款金额必须等于商品售价 %s 分' % product.price_cents)

    paid_at = parse_dt(data['paid_at']) if data.get('paid_at') else now_naive()

    pay = PaymentRecord(
        id=random_id(), tenant_id=tenant_id, idempotency_key=idem_key or None,
        receipt_no=data.get('receipt_no', '') or ('R' + now_naive().strftime('%y%m%d%H%M%S')),
        store_id=store_id, member_id=member_id, product_id=product_id,
        product_name=product.name, amount_cents=amount_cents, paid_at=paid_at,
        method=method, operator_id=operator['id'], remark=data.get('remark', ''),
    )
    db.add(pay)

    # 生成权益实例
    ent = Entitlement(
        id=random_id(), tenant_id=tenant_id, member_id=member_id, product_id=product_id,
        product_name=product.name, ptype=product.ptype,
        total_sessions=product.total_sessions, used_sessions=0,
        price_cents=product.price_cents,
        balance_cents=amount_cents if product.ptype == 'STORED' else 0,
        expires_at=now_naive() + timedelta(days=product.valid_days),
        source_payment_id=pay.id,
    )
    db.add(ent)
    pay.entitlement_id = ent.id

    # 权益流水（PURCHASE）
    db.add(LedgerEntry(
        id=random_id(), tenant_id=tenant_id, idempotency_key=idem_key or None,
        member_id=member_id, entitlement_id=ent.id, kind='PURCHASE',
        delta_sessions=product.total_sessions, delta_cents=amount_cents,
        balance_sessions=product.total_sessions,
        balance_cents=ent.balance_cents,
        ref_type='PAYMENT', ref_id=pay.id, remark='购买 %s' % product.name,
        operator_id=operator['id'],
    ))

    write_audit(db, tenant_id, operator['id'], operator['name'], 'PAY', 'payment', pay.id,
                {'member_id': member_id, 'product': product.name, 'amount_cents': amount_cents,
                 'method': method})
    return _payment_payload(pay)


def _payment_payload(pay: PaymentRecord) -> Dict:
    return {
        'id': pay.id, 'receipt_no': pay.receipt_no, 'store_id': pay.store_id,
        'member_id': pay.member_id, 'product_id': pay.product_id,
        'product_name': pay.product_name, 'amount_cents': pay.amount_cents,
        'paid_at': pay.paid_at.strftime('%Y-%m-%d %H:%M'), 'method': pay.method,
        'entitlement_id': pay.entitlement_id, 'status': pay.status,
        'operator_id': pay.operator_id, 'remark': pay.remark,
    }


def list_payments(db: Session, tenant_id: str, store_id: str = '', member_id: str = '',
                  limit: int = 100) -> List[Dict]:
    stmt = select(PaymentRecord).where(
        PaymentRecord.tenant_id == tenant_id, PaymentRecord.status == 'PAID')
    if store_id:
        stmt = stmt.where(PaymentRecord.store_id == store_id)
    if member_id:
        stmt = stmt.where(PaymentRecord.member_id == member_id)
    stmt = stmt.order_by(PaymentRecord.paid_at.desc()).limit(limit)
    return [_payment_payload(p) for p in db.scalars(stmt).all()]


# ---------- 退款 ----------
def refund(db: Session, tenant_id: str, operator: Dict, entitlement_id: str, remark: str = '') -> Dict:
    ent = db.get(Entitlement, entitlement_id)
    if not ent or ent.tenant_id != tenant_id:
        raise NotFound('权益不存在')
    if ent.status != 'ACTIVE':
        raise BusinessRuleError('该权益已不可退款（已用完/过期/已退）')
    if ent.ptype == 'STORED':
        refund_cents = ent.balance_cents
        formula = '储值余额全额退款'
    else:
        refund_cents = refund_amount_cents(ent.price_cents, ent.total_sessions, ent.used_sessions)
        formula = refund_formula_text(ent.price_cents, ent.total_sessions, ent.used_sessions)
    if refund_cents <= 0:
        raise BusinessRuleError('无可退金额')

    ref = RefundRecord(
        id=random_id(), tenant_id=tenant_id, payment_id=ent.source_payment_id,
        entitlement_id=ent.id, member_id=ent.member_id, refund_cents=refund_cents,
        formula=formula, operator_id=operator['id'],
    )
    db.add(ref)

    ent.status = 'REFUNDED'
    db.add(LedgerEntry(
        id=random_id(), tenant_id=tenant_id, member_id=ent.member_id, entitlement_id=ent.id,
        kind='REFUND', delta_sessions=-(ent.total_sessions - ent.used_sessions),
        delta_cents=-refund_cents, balance_sessions=0, balance_cents=0,
        ref_type='REFUND', ref_id=ref.id, remark=remark or formula, operator_id=operator['id'],
    ))
    ref.ledger_entry_id = ref.id

    write_audit(db, tenant_id, operator['id'], operator['name'], 'REFUND', 'refund', ref.id,
                {'entitlement_id': ent.id, 'refund_cents': refund_cents, 'formula': formula})
    return {'id': ref.id, 'refund_cents': refund_cents, 'formula': formula}


# ---------- 会员权益 / 流水 ----------
def member_entitlements(db: Session, tenant_id: str, member_id: str) -> List[Dict]:
    rows = db.scalars(select(Entitlement).where(
        Entitlement.tenant_id == tenant_id, Entitlement.member_id == member_id)
        .order_by(Entitlement.expires_at.asc(), Entitlement.created_at.asc())).all()
    out = []
    for e in rows:
        out.append({
            'id': e.id, 'product_name': e.product_name, 'ptype': e.ptype,
            'total_sessions': e.total_sessions, 'used_sessions': e.used_sessions,
            'remain_sessions': e.total_sessions - e.used_sessions,
            'balance_cents': e.balance_cents,
            'expires_at': e.expires_at.strftime('%Y-%m-%d') if e.expires_at else '',
            'status': e.status,
        })
    return out


def member_ledger(db: Session, tenant_id: str, member_id: str, limit: int = 100) -> List[Dict]:
    rows = db.scalars(select(LedgerEntry).where(
        LedgerEntry.tenant_id == tenant_id, LedgerEntry.member_id == member_id)
        .order_by(LedgerEntry.created_at.desc()).limit(limit)).all()
    # FR-03：会员端流水可见操作人与备注（只读，不可修改）
    out = []
    for r in rows:
        op = db.get(User, r.operator_id) if r.operator_id else None
        out.append({
            'id': r.id, 'kind': r.kind, 'delta_sessions': r.delta_sessions,
            'delta_cents': r.delta_cents, 'balance_sessions': r.balance_sessions,
            'balance_cents': r.balance_cents, 'remark': r.remark,
            'created_at': r.created_at.strftime('%Y-%m-%d %H:%M'),
            'operator_id': r.operator_id,
            'operator_name': op.real_name if op else '',
        })
    return out
