"""账务回归（T-12）：退款红冲 / 幂等 / 超额 / 作废 / 日终台账 / 负余额口径。

覆盖计划里的 6 组验收：
1. 全额退 → 收入减少且单据 REFUNDED；
2. 部分退 1/4 → 金额 = 售价/4 且 PARTIAL_REFUNDED；
3. 幂等重复退 → 不重复扣钱；
4. 超退 → 422；
5. VOID 后列表可见且收入回落；已消课/已退款不可作废 → 422；
6. 日终台账 refund_cents 落库，负余额检测不因退款误报。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (DailyLedger, Entitlement, LedgerEntry, PaymentRecord,
                        ReconciliationDiff, RefundRecord)
from app.services import dashboard as dash
from app.utils.timeutil import business_date


def _register(client, headers, *, store_id, member_id, product_id, amount_cents,
              key: str = '', method: str = 'CASH') -> dict:
    resp = client.post('/api/payments', headers=headers, json={
        'store_id': store_id, 'member_id': member_id, 'product_id': product_id,
        'amount_cents': amount_cents, 'method': method, 'idempotency_key': key})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _refund(client, headers, **body):
    return client.post('/api/payments/refund', headers=headers, json=body)


def _overview(client, headers) -> dict:
    resp = client.get('/api/dashboard/overview', headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _pay_package(client, world, tokens, *, total_sessions: int = 10,
                 price_cents: int = 199900) -> dict:
    factory = world['factory']
    member_id = factory.member(world['tenant_a'])
    product_id = factory.product(world['tenant_a'], 'PACKAGE', total_sessions, price_cents)
    return _register(client, tokens['a'], store_id=world['store_a'], member_id=member_id,
                     product_id=product_id, amount_cents=price_cents)


# ---------- ① 全额退 ----------
def test_full_refund_reduces_income(client, world, tokens):
    pay = _pay_package(client, world, tokens)
    assert pay['status'] == 'PAID'
    assert pay['refunded_cents'] == 0
    assert pay['refundable_cents'] == 199900

    before = _overview(client, tokens['a'])
    resp = _refund(client, tokens['a'], entitlement_id=pay['entitlement_id'])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['refund_cents'] == 199900
    assert body['payment_status'] == 'REFUNDED'
    assert body['refundable_cents'] == 0
    assert body['ledger_entry_id'], '退款必须写流水并回写 ledger_entry_id'

    after = _overview(client, tokens['a'])
    assert after['month_income_cents'] == before['month_income_cents'] - 199900
    assert after['month_refund_cents'] == before['month_refund_cents'] + 199900
    assert after['month_gross_cents'] == before['month_gross_cents']

    with SessionLocal() as db:
        stored = db.get(PaymentRecord, pay['id'])
        assert stored.status == 'REFUNDED'
        assert stored.refunded_cents == 199900
        ref = db.scalar(select(RefundRecord).where(RefundRecord.payment_id == pay['id']))
        ledger = db.get(LedgerEntry, ref.ledger_entry_id)
        assert ledger is not None, 'ledger_entry_id 必须指向真实流水（历史 bug 写的是退款单 ID）'
        assert ledger.kind == 'REFUND'
        assert ledger.delta_cents == -199900
        assert ledger.delta_sessions == -10

    listed = client.get('/api/payments', headers=tokens['a']).json()
    row = next(p for p in listed if p['id'] == pay['id'])
    assert row['status'] == 'REFUNDED'
    assert row['refunded_cents'] == 199900
    assert row['refundable_cents'] == 0

    # status 可选过滤：精确命中已退款单
    refunded_only = client.get('/api/payments?status=REFUNDED', headers=tokens['a']).json()
    assert refunded_only, '按状态过滤没有结果'
    assert all(p['status'] == 'REFUNDED' for p in refunded_only)
    assert any(p['id'] == pay['id'] for p in refunded_only)


# ---------- ② 部分退 1/4 ----------
def test_partial_refund_quarter(client, world, tokens):
    factory = world['factory']
    member_id = factory.member(world['tenant_a'])
    product_id = factory.product(world['tenant_a'], 'PACKAGE', 4, 199900)
    pay = _register(client, tokens['a'], store_id=world['store_a'], member_id=member_id,
                    product_id=product_id, amount_cents=199900)

    resp = _refund(client, tokens['a'], entitlement_id=pay['entitlement_id'], refund_sessions=1)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['refund_cents'] == 49975, '199900 ÷ 4 应为 49975'
    assert body['refund_sessions'] == 1
    assert body['payment_status'] == 'PARTIAL_REFUNDED'
    assert body['refunded_cents'] == 49975
    assert body['refundable_cents'] == 199900 - 49975

    with SessionLocal() as db:
        stored = db.get(PaymentRecord, pay['id'])
        assert stored.status == 'PARTIAL_REFUNDED'
        ent = db.get(Entitlement, pay['entitlement_id'])
        assert ent.status == 'ACTIVE', '部分退款不应终止权益'
        assert ent.used_sessions == 1, '部分退款应把退掉的课时计回已用'


# ---------- ③ 幂等 ----------
def test_refund_idempotent(client, world, tokens):
    pay = _pay_package(client, world, tokens, total_sessions=4, price_cents=199900)
    key = 'refund-key-' + uuid.uuid4().hex[:12]

    first = _refund(client, tokens['a'], entitlement_id=pay['entitlement_id'],
                    refund_sessions=1, idempotency_key=key)
    assert first.status_code == 200, first.text
    second = _refund(client, tokens['a'], entitlement_id=pay['entitlement_id'],
                     refund_sessions=1, idempotency_key=key)
    assert second.status_code == 200, second.text
    assert second.json()['id'] == first.json()['id']
    assert second.json()['idempotent'] is True
    assert second.json()['refunded_cents'] == first.json()['refunded_cents'] == 49975

    with SessionLocal() as db:
        rows = db.scalars(select(RefundRecord).where(
            RefundRecord.idempotency_key == key)).all()
        assert len(rows) == 1, '同幂等键写了多张退款单'
        assert db.get(PaymentRecord, pay['id']).refunded_cents == 49975


# ---------- ④ 超退 ----------
def test_over_refund_422(client, world, tokens):
    factory = world['factory']
    member_id = factory.member(world['tenant_a'])
    product_id = factory.product(world['tenant_a'], 'STORED', 0, 50000)
    pay = _register(client, tokens['a'], store_id=world['store_a'], member_id=member_id,
                    product_id=product_id, amount_cents=50000)

    resp = _refund(client, tokens['a'], entitlement_id=pay['entitlement_id'], refund_cents=50001)
    assert resp.status_code == 422, resp.text
    assert resp.json()['error']['code'] == 'BUSINESS_RULE_VIOLATION'

    # 课时型超课时同样 422
    pkg = _pay_package(client, world, tokens, total_sessions=4, price_cents=199900)
    resp2 = _refund(client, tokens['a'], entitlement_id=pkg['entitlement_id'], refund_sessions=5)
    assert resp2.status_code == 422, resp2.text

    with SessionLocal() as db:
        assert db.get(PaymentRecord, pay['id']).refunded_cents == 0


def test_stored_refund_by_amount(client, world, tokens):
    factory = world['factory']
    member_id = factory.member(world['tenant_a'])
    product_id = factory.product(world['tenant_a'], 'STORED', 0, 50000)
    pay = _register(client, tokens['a'], store_id=world['store_a'], member_id=member_id,
                    product_id=product_id, amount_cents=50000)

    resp = _refund(client, tokens['a'], entitlement_id=pay['entitlement_id'], refund_cents=20000)
    assert resp.status_code == 200, resp.text
    assert resp.json()['refund_cents'] == 20000
    assert resp.json()['payment_status'] == 'PARTIAL_REFUNDED'
    with SessionLocal() as db:
        ent = db.get(Entitlement, pay['entitlement_id'])
        assert ent.balance_cents == 30000

    # 按课时退储值卡属于参数错配 → 400
    resp2 = _refund(client, tokens['a'], entitlement_id=pay['entitlement_id'], refund_sessions=1)
    assert resp2.status_code == 400, resp2.text


# ---------- ⑤ 作废 ----------
def test_void_payment_visible_in_list(client, world, tokens):
    before = _overview(client, tokens['a'])
    pay = _pay_package(client, world, tokens)
    paid = _overview(client, tokens['a'])
    assert paid['month_income_cents'] == before['month_income_cents'] + 199900

    resp = client.post('/api/payments/%s/void' % pay['id'], headers=tokens['a'],
                       json={'remark': '录错金额'})
    assert resp.status_code == 200, resp.text
    assert resp.json()['status'] == 'VOID'

    listed = client.get('/api/payments', headers=tokens['a']).json()
    row = next(p for p in listed if p['id'] == pay['id'])
    assert row['status'] == 'VOID', '作废单必须在列表可见，否则无法追溯'

    # 作废后这笔钱不再计入收入
    voided = _overview(client, tokens['a'])
    assert voided['month_income_cents'] == before['month_income_cents']

    with SessionLocal() as db:
        stored = db.get(PaymentRecord, pay['id'])
        assert stored.status == 'VOID'
        ent = db.get(Entitlement, stored.entitlement_id)
        assert ent.status == 'REFUNDED'

    # 重复作废 → 422
    again = client.post('/api/payments/%s/void' % pay['id'], headers=tokens['a'], json={})
    assert again.status_code == 422


def test_void_after_consume_422(client, world, tokens):
    pay = _pay_package(client, world, tokens)
    with SessionLocal() as db:
        ent = db.get(Entitlement, pay['entitlement_id'])
        ent.used_sessions = 1
        db.commit()
    resp = client.post('/api/payments/%s/void' % pay['id'], headers=tokens['a'], json={})
    assert resp.status_code == 422, resp.text
    with SessionLocal() as db:
        assert db.get(PaymentRecord, pay['id']).status == 'PAID'


def test_void_refunded_payment_422(client, world, tokens):
    pay = _pay_package(client, world, tokens, total_sessions=4, price_cents=199900)
    _refund(client, tokens['a'], entitlement_id=pay['entitlement_id'], refund_sessions=1)
    resp = client.post('/api/payments/%s/void' % pay['id'], headers=tokens['a'], json={})
    assert resp.status_code == 422, resp.text


# ---------- ⑥ 日终台账 ----------
def test_daily_ledger_refund(client, world, tokens):
    date = business_date()
    before = client.post('/api/dashboard/reconcile', headers=tokens['a'],
                         json={'store_id': world['store_a'], 'date': date}).json()

    pay = _pay_package(client, world, tokens, total_sessions=4, price_cents=199900)
    _refund(client, tokens['a'], entitlement_id=pay['entitlement_id'], refund_sessions=1)

    after = client.post('/api/dashboard/reconcile', headers=tokens['a'],
                        json={'store_id': world['store_a'], 'date': date}).json()
    assert after['refund_count'] == before['refund_count'] + 1
    assert after['refund_cents'] == before['refund_cents'] + 49975
    assert after['net_income_cents'] == after['purchase_cents'] - after['refund_cents']

    with SessionLocal() as db:
        row = db.scalar(select(DailyLedger).where(
            DailyLedger.tenant_id == world['tenant_a'], DailyLedger.store_id == world['store_a'],
            DailyLedger.biz_date == date))
        assert row is not None
        assert row.refund_cents == after['refund_cents']
        assert row.refund_count == after['refund_count']
        assert row.active_entitlements == after['active_entitlements']

    # 重跑幂等：同店同日只保留一条快照
    again = client.post('/api/dashboard/reconcile', headers=tokens['a'],
                        json={'store_id': world['store_a'], 'date': date}).json()
    assert again['refund_cents'] == after['refund_cents']
    with SessionLocal() as db:
        rows = db.scalars(select(DailyLedger).where(
            DailyLedger.tenant_id == world['tenant_a'], DailyLedger.store_id == world['store_a'],
            DailyLedger.biz_date == date)).all()
        assert len(rows) == 1


def test_negative_balance_ignores_refund_ledger(world):
    """负余额只统计 kind='CONSUME'：退款/冲正也是负向流水，不能算成消课。

    构造：买 2 节 → 消课 2 节 → 退款流水 -2。
    旧口径把三类负流水相加得 consumed=4 > bought=2，会误报负余额；
    新口径 consumed=2 <= bought=2，不报警。
    """
    factory = world['factory']
    member_id = factory.member(world['tenant_a'])
    with SessionLocal() as db:
        db.add(LedgerEntry(
            id=uuid.uuid4().hex, tenant_id=world['tenant_a'], member_id=member_id,
            entitlement_id='ent-x', kind='PURCHASE', delta_sessions=2, delta_cents=10000,
            balance_sessions=2, balance_cents=0, ref_type='PAYMENT', ref_id='pay-x',
            remark='购买', operator_id=''))
        db.add(LedgerEntry(
            id=uuid.uuid4().hex, tenant_id=world['tenant_a'], member_id=member_id,
            entitlement_id='ent-x', kind='CONSUME', delta_sessions=-2, delta_cents=0,
            balance_sessions=0, balance_cents=0, ref_type='ATTENDANCE', ref_id='s-x',
            remark='消课', operator_id=''))
        db.add(LedgerEntry(
            id=uuid.uuid4().hex, tenant_id=world['tenant_a'], member_id=member_id,
            entitlement_id='ent-x', kind='REFUND', delta_sessions=-2, delta_cents=-10000,
            balance_sessions=0, balance_cents=0, ref_type='REFUND', ref_id='ref-x',
            remark='退款', operator_id=''))
        db.commit()

        dash.reconcile_daily(db, world['tenant_a'], world['store_a'], business_date())
        db.commit()
        diffs = list(db.scalars(select(ReconciliationDiff).where(
            ReconciliationDiff.tenant_id == world['tenant_a'],
            ReconciliationDiff.ref_id == member_id)).all())
    assert diffs == [], '退款流水被误判为负余额'


# ---------- T-06 幂等键按租户收口 ----------
def test_idem_scope(client, world, tokens):
    """双租户使用同一幂等键都能成功：唯一约束必须带 tenant_id。"""
    factory = world['factory']
    key = 'shared-key-' + uuid.uuid4().hex[:12]

    member_a = factory.member(world['tenant_a'])
    product_a = factory.product(world['tenant_a'], 'PACKAGE', 10, 100000)
    pay_a = _register(client, tokens['a'], store_id=world['store_a'], member_id=member_a,
                      product_id=product_a, amount_cents=100000, key=key)

    member_b = factory.member(world['tenant_b'])
    product_b = factory.product(world['tenant_b'], 'PACKAGE', 10, 100000)
    pay_b = _register(client, tokens['b'], store_id=world['store_b'], member_id=member_b,
                      product_id=product_b, amount_cents=100000, key=key)

    assert pay_a['id'] != pay_b['id']
    with SessionLocal() as db:
        rows = db.scalars(select(PaymentRecord).where(
            PaymentRecord.idempotency_key == key)).all()
        assert len(rows) == 2
        assert {r.tenant_id for r in rows} == {world['tenant_a'], world['tenant_b']}
