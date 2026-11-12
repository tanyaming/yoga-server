"""收款登记 / 权益账本 / 退款：账实一致底线的核心服务。

口径（PRD 已确认）：无在线支付，线下收款、线上记账。
- 收款登记幂等：幂等键重复且参数一致 → 返回原单；参数不一致 → 冲突。
- 收款即生成权益实例 + 权益流水（PURCHASE），金额一律「分」存储。
- 退款支持三种入口：全额退（不传参数）、按课时退（课时型用 refund_sessions）、
  按金额退（储值卡用 refund_cents）；折算过程写入 formula 字段可复核。
- 退款幂等：同一 ``(tenant_id, idempotency_key)`` 重放返回原退款单，绝不重复扣钱。
- 收入口径：``amount_cents - refunded_cents``，因此退款必须回写收款单，否则账实永远对不上。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import (BusinessRuleError, Conflict, IdempotencyConflict, NotFound,
                      ValidationError)
from ..models import (Entitlement, LedgerEntry, Member, PaymentRecord, Product,
                      RefundRecord, User)
from ..repositories.base import write_audit
from ..security import random_id
from ..utils.money import (format_cents, prorate_refund_cents,
                           prorate_refund_formula_text)
from ..utils.timeutil import now_naive, parse_dt

_PAY_METHODS = ('CASH', 'ALIPAY', 'WECHAT', 'CARD', 'OTHER')

# 收款单列表默认可见的状态：VOID 也要可见，否则作废单在后台「凭空消失」无法追溯
DEFAULT_LIST_STATUSES = ('PAID', 'PARTIAL_REFUNDED', 'REFUNDED', 'VOID')


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
    applicable = [x.strip() for x in str(data.get('applicable_store_ids') or '').split(',') if x.strip()]
    ctypes = [x.strip().upper() for x in str(data.get('allowed_ctypes') or '').split(',') if x.strip()]
    for c in ctypes:
        if c not in ('GROUP', 'PRIVATE'):
            raise ValidationError('可约课型非法：%s' % c)
    p = Product(id=random_id(), tenant_id=tenant_id, code=code, name=name, ptype=ptype,
                total_sessions=total_sessions, price_cents=price_cents,
                valid_days=int(data.get('valid_days', 365) or 365),
                cross_store=bool(data.get('cross_store', False)),
                applicable_store_ids=','.join(applicable),
                allowed_ctypes=','.join(ctypes),
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
        existing = _find_payment_by_key(db, tenant_id, idem_key)
        if existing:
            return _replay_payment(existing, member_id, product_id, amount_cents)

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
        # 显式给状态与累计退款额：Python 侧 default 要等到 flush 才生效，
        # 否则接口回显的 status 会是 None（历史遗留问题）
        status='PAID', refunded_cents=0,
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
        # FR-16：商品适用规则在收款时快照到权益实例，商品后续改配置不影响已售权益
        cross_store=product.cross_store,
        applicable_store_ids=product.applicable_store_ids,
        allowed_ctypes=product.allowed_ctypes,
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
    # 先 flush 把唯一约束的冲突暴露在这里，才能转成 409 / 幂等重放而不是直落 500。
    # 串行预检挡不住并发：两个线程可能同时通过「查无此键」，最终由约束裁决。
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        dup = _find_payment_by_key(db, tenant_id, idem_key) if idem_key else None
        if dup is not None:
            return _replay_payment(dup, member_id, product_id, amount_cents)
        # 非幂等键冲突（如唯一票据号并发写入）：明确报冲突，不返回 500
        raise Conflict('收款单写入冲突，请稍后重试')
    return _payment_payload(pay)


def _find_payment_by_key(db: Session, tenant_id: str,
                         idem_key: str) -> Optional[PaymentRecord]:
    if not idem_key:
        return None
    return db.scalar(select(PaymentRecord).where(
        PaymentRecord.idempotency_key == idem_key,
        PaymentRecord.tenant_id == tenant_id))


def _replay_payment(existing: PaymentRecord, member_id: str, product_id: str,
                    amount_cents: int) -> Dict:
    """幂等命中：参数一致返回原单，参数不一致报 409（同一键不允许两套业务参数）。"""
    if (existing.member_id != member_id or existing.product_id != product_id
            or int(existing.amount_cents) != int(amount_cents)):
        raise IdempotencyConflict()
    return _payment_payload(existing, idempotent=True)


def _payment_payload(pay: PaymentRecord, idempotent: bool = False) -> Dict:
    refunded = int(pay.refunded_cents or 0)
    return {
        'id': pay.id, 'receipt_no': pay.receipt_no, 'store_id': pay.store_id,
        'member_id': pay.member_id, 'product_id': pay.product_id,
        'product_name': pay.product_name, 'amount_cents': pay.amount_cents,
        'paid_at': pay.paid_at.strftime('%Y-%m-%d %H:%M'), 'method': pay.method,
        'entitlement_id': pay.entitlement_id, 'status': pay.status,
        'refunded_cents': refunded,
        'refundable_cents': max(0, int(pay.amount_cents) - refunded),
        'operator_id': pay.operator_id, 'remark': pay.remark,
        'idempotent': bool(idempotent),
    }


def list_payments(db: Session, tenant_id: str, store_id: str = '', member_id: str = '',
                  status: str = '', limit: int = 100) -> List[Dict]:
    """收款单列表。

    ``status`` 为可选精确过滤；不传时返回 PAID/PARTIAL_REFUNDED/REFUNDED/VOID ——
    既覆盖「已收款与已退款」，也保证作废单可见可追溯（旧实现硬过滤 PAID，退款单直接消失）。
    """
    stmt = select(PaymentRecord).where(PaymentRecord.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(PaymentRecord.status == status)
    else:
        stmt = stmt.where(PaymentRecord.status.in_(DEFAULT_LIST_STATUSES))
    if store_id:
        stmt = stmt.where(PaymentRecord.store_id == store_id)
    if member_id:
        stmt = stmt.where(PaymentRecord.member_id == member_id)
    stmt = stmt.order_by(PaymentRecord.paid_at.desc()).limit(limit)
    return [_payment_payload(p) for p in db.scalars(stmt).all()]


# ---------- 退款 ----------
def _refund_payload(db: Session, ref: RefundRecord, idempotent: bool = False) -> Dict:
    pay = db.get(PaymentRecord, ref.payment_id) if ref.payment_id else None
    refunded = int(pay.refunded_cents or 0) if pay else 0
    return {
        'id': ref.id, 'payment_id': ref.payment_id, 'entitlement_id': ref.entitlement_id,
        'member_id': ref.member_id, 'store_id': ref.store_id,
        'refund_cents': int(ref.refund_cents), 'refund_sessions': int(ref.refund_sessions or 0),
        'formula': ref.formula, 'status': ref.status,
        'ledger_entry_id': ref.ledger_entry_id,
        'payment_status': pay.status if pay else '',
        'refunded_cents': refunded,
        'refundable_cents': max(0, int(pay.amount_cents) - refunded) if pay else 0,
        'idempotent': idempotent,
    }


def _find_refund_by_key(db: Session, tenant_id: str, idem_key: str) -> Optional[RefundRecord]:
    if not idem_key:
        return None
    return db.scalar(select(RefundRecord).where(
        RefundRecord.tenant_id == tenant_id,
        RefundRecord.idempotency_key == idem_key))


def refund(db: Session, tenant_id: str, operator: Dict, entitlement_id: str,
           remark: str = '', refund_sessions: Optional[int] = None,
           refund_cents: Optional[int] = None, idempotency_key: str = '',
           store_id: str = '') -> Dict:
    """退款/红冲：全额、按课时、按金额三种口径，回写收款单并写 REFUND 负向流水。

    参数:
        refund_sessions: 课时型（PACKAGE/PRIVATE）按课时退，``None`` 表示不按课时退。
        refund_cents: 储值型（STORED）按金额退，``None`` 表示不按金额退。
        二者都不传：全额退（课时型退剩余课时，储值型退全部余额）。
    """
    idem_key = (idempotency_key or '').strip()

    # 幂等短路：同键直接返回原单，不重复扣钱（必须在任何写操作之前）
    existing = _find_refund_by_key(db, tenant_id, idem_key)
    if existing is not None:
        return _refund_payload(db, existing, idempotent=True)

    ent = db.get(Entitlement, entitlement_id)
    if not ent or ent.tenant_id != tenant_id:
        raise NotFound('权益不存在')
    if ent.status != 'ACTIVE':
        raise BusinessRuleError('该权益已不可退款（已用完/已过期/已退）')

    pay = db.get(PaymentRecord, ent.source_payment_id) if ent.source_payment_id else None
    if not pay or pay.tenant_id != tenant_id:
        raise NotFound('该权益没有关联的收款单，无法退款')
    if pay.status == 'VOID':
        raise BusinessRuleError('收款单已作废，不可退款')
    if int(pay.refunded_cents or 0) >= int(pay.amount_cents):
        raise BusinessRuleError('该收款单已全额退款')

    is_stored = ent.ptype == 'STORED'
    remain_sessions = max(0, int(ent.total_sessions) - int(ent.used_sessions))
    full_refund = refund_sessions is None and refund_cents is None
    if refund_sessions is not None and refund_cents is not None:
        # 两种口径互斥：同时传入无法判断以哪个为准，直接拒绝而不是静默取其一
        raise ValidationError('refund_sessions 与 refund_cents 不能同时传入')

    if is_stored:
        if refund_sessions is not None:
            raise ValidationError('储值卡不支持按课时退款，请使用 refund_cents')
        amount = int(ent.balance_cents) if refund_cents is None else int(refund_cents)
        if amount <= 0:
            raise ValidationError('退款金额必须大于 0')
        if amount > int(ent.balance_cents):
            raise BusinessRuleError('退款金额超出储值余额 %s' % format_cents(ent.balance_cents))
        sessions_to_deduct = 0
        full_refund = amount >= int(ent.balance_cents)
        formula = '储值余额退款 %s（余额 %s）' % (format_cents(amount),
                                                 format_cents(ent.balance_cents))
    else:
        if refund_cents is not None and refund_sessions is None:
            raise ValidationError('课时型退款请指定 refund_sessions（课时数）')
        if int(ent.total_sessions) <= 0:
            raise BusinessRuleError('该权益总课时为 0，无法按课时退款')
        sessions_to_deduct = remain_sessions if full_refund else int(refund_sessions)
        if sessions_to_deduct <= 0:
            raise BusinessRuleError('没有可退课时')
        if sessions_to_deduct > remain_sessions:
            raise BusinessRuleError('退款课时 %d 节超过剩余课时 %d 节'
                                    % (sessions_to_deduct, remain_sessions))
        amount = prorate_refund_cents(ent.price_cents, ent.total_sessions, sessions_to_deduct)
        if amount <= 0:
            raise BusinessRuleError('无可退金额')
        full_refund = sessions_to_deduct >= remain_sessions
        formula = prorate_refund_formula_text(ent.price_cents, ent.total_sessions,
                                              sessions_to_deduct)

    # 超退硬校验：累计已退 + 本次 <= 收款金额
    already = int(pay.refunded_cents or 0)
    if already + amount > int(pay.amount_cents):
        raise BusinessRuleError(
            '退款金额超出可退上限（收款 %s，已退 %s，本次 %s）'
            % (format_cents(pay.amount_cents), format_cents(already), format_cents(amount)))

    ref = RefundRecord(
        id=random_id(), tenant_id=tenant_id, idempotency_key=idem_key or None,
        payment_id=pay.id, entitlement_id=ent.id, member_id=ent.member_id,
        store_id=store_id or pay.store_id, refund_cents=amount,
        refund_sessions=sessions_to_deduct, formula=formula, status='DONE',
        operator_id=operator['id'],
    )
    db.add(ref)
    try:
        db.flush()
    except IntegrityError:
        # 并发同键：另一笔先落地，退化为幂等返回，不重复扣钱
        db.rollback()
        dup = _find_refund_by_key(db, tenant_id, idem_key)
        if dup is not None:
            return _refund_payload(db, dup, idempotent=True)
        raise Conflict('退款单写入冲突，请重试')

    # 回写收款单：状态机 + 累计退款额
    pay.refunded_cents = already + amount
    if int(pay.refunded_cents) >= int(pay.amount_cents):
        pay.refunded_cents = int(pay.amount_cents)
        pay.status = 'REFUNDED'
    else:
        pay.status = 'PARTIAL_REFUNDED'

    # 权益口径：全额退终止权益；部分退把退掉的课时计回 used，退满即 DEPLETED
    if is_stored:
        ent.balance_cents = max(0, int(ent.balance_cents) - amount)
        if full_refund:
            ent.balance_cents = 0
            ent.status = 'REFUNDED'
        elif int(ent.balance_cents) <= 0:
            ent.status = 'DEPLETED'
    elif full_refund:
        ent.status = 'REFUNDED'
    else:
        ent.used_sessions = min(int(ent.total_sessions),
                                int(ent.used_sessions) + sessions_to_deduct)
        if int(ent.used_sessions) >= int(ent.total_sessions):
            ent.status = 'DEPLETED'

    # REFUND 负向流水：幂等键加前缀，避免与 PURCHASE 同键撞唯一约束
    ledger = LedgerEntry(
        id=random_id(), tenant_id=tenant_id,
        idempotency_key=('refund:' + (idem_key or ref.id))[:64],
        member_id=ent.member_id, entitlement_id=ent.id, kind='REFUND',
        delta_sessions=-sessions_to_deduct, delta_cents=-amount,
        balance_sessions=max(0, int(ent.total_sessions) - int(ent.used_sessions)),
        balance_cents=int(ent.balance_cents),
        ref_type='REFUND', ref_id=ref.id, remark=remark or formula,
        operator_id=operator['id'],
    )
    db.add(ledger)
    db.flush()
    # 历史 bug：这里曾写成 ref.ledger_entry_id = ref.id，导致按该字段回溯流水全部落空
    ref.ledger_entry_id = ledger.id

    write_audit(db, tenant_id, operator['id'], operator['name'], 'REFUND', 'refund', ref.id,
                {'entitlement_id': ent.id, 'payment_id': pay.id, 'refund_cents': amount,
                 'refund_sessions': sessions_to_deduct, 'formula': formula,
                 'payment_status': pay.status})
    return _refund_payload(db, ref)


# ---------- 作废 ----------
def void_payment(db: Session, tenant_id: str, operator: Dict, payment_id: str,
                 remark: str = '') -> Dict:
    """作废收款单（录错单冲正）：仅「未消课、未退款」的收款单可作废。

    作废后该单退出收入口径（status=VOID），权益同步终止，流水写 ADJUST 负数保持可重放。
    """
    pay = db.get(PaymentRecord, payment_id)
    if not pay or pay.tenant_id != tenant_id:
        raise NotFound('收款单不存在')
    if pay.status == 'VOID':
        raise BusinessRuleError('该收款单已作废')
    if int(pay.refunded_cents or 0) > 0 or pay.status in ('PARTIAL_REFUNDED', 'REFUNDED'):
        raise BusinessRuleError('该收款单已发生退款，不可作废，请使用退款流程')

    ent = db.get(Entitlement, pay.entitlement_id) if pay.entitlement_id else None
    if ent is not None and ent.tenant_id != tenant_id:
        ent = None
    if ent is not None:
        if int(ent.used_sessions or 0) > 0:
            raise BusinessRuleError('该权益已消课 %d 节，不可作废' % int(ent.used_sessions))
        consumed = db.scalar(select(func.count(LedgerEntry.id)).where(
            LedgerEntry.tenant_id == tenant_id, LedgerEntry.entitlement_id == ent.id,
            LedgerEntry.kind == 'CONSUME')) or 0
        if consumed:
            raise BusinessRuleError('该权益已有消课流水，不可作废')

    pay.status = 'VOID'
    note = (remark or '').strip()
    if note:
        pay.remark = (pay.remark + ' | ' + note) if pay.remark else note[:240]

    if ent is not None:
        remain = max(0, int(ent.total_sessions) - int(ent.used_sessions))
        db.add(LedgerEntry(
            id=random_id(), tenant_id=tenant_id, member_id=ent.member_id,
            entitlement_id=ent.id, kind='ADJUST', delta_sessions=-remain,
            delta_cents=-int(ent.balance_cents),
            balance_sessions=0, balance_cents=0,
            ref_type='PAYMENT', ref_id=pay.id,
            remark='收款单作废冲正', operator_id=operator['id'],
        ))
        ent.status = 'REFUNDED'
        ent.balance_cents = 0

    write_audit(db, tenant_id, operator['id'], operator['name'], 'VOID', 'payment', pay.id,
                {'entitlement_id': pay.entitlement_id, 'remark': note})
    return {'id': pay.id, 'status': pay.status, 'entitlement_id': pay.entitlement_id,
            'refunded_cents': int(pay.refunded_cents or 0)}


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
