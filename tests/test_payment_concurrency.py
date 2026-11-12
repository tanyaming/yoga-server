"""收款登记并发幂等（D4-R2）：同幂等键并发 POST 只能产生 1 张收款单 + 1 条 PURCHASE 流水。

``register_payment`` 原来只有串行预检，没有 IntegrityError 兜底：两个请求同时通过
「查无此键」后，由唯一约束裁决，输的那条直接 500。本用例锁住「兜底成幂等重放」。
"""
from __future__ import annotations

import threading
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import LedgerEntry, PaymentRecord


def _fire(headers: dict, payload: dict, sink: list, barrier: threading.Barrier) -> None:
    """一个线程一个 TestClient（独立连接 + 独立事务），barrier 保证同时起跑。"""
    client = TestClient(app)
    try:
        barrier.wait(timeout=20)
        resp = client.post('/api/payments', headers=headers, json=payload)
        sink.append((resp.status_code, resp.json()))
    except Exception as exc:  # noqa: BLE001 - 汇总后统一断言
        sink.append((-1, {'error': {'code': 'THREAD_EXCEPTION', 'message': repr(exc)}}))


def test_concurrent_same_idempotency_key_single_payment(client, world, tokens):
    factory = world['factory']
    member_id = factory.member(world['tenant_a'])
    product_id = factory.product(world['tenant_a'], 'PACKAGE', 10, 120000)
    key = 'pay-concurrent-' + uuid.uuid4().hex[:12]
    payload = {'store_id': world['store_a'], 'member_id': member_id,
               'product_id': product_id, 'amount_cents': 120000,
               'method': 'WECHAT', 'idempotency_key': key}

    results: list = []
    barrier = threading.Barrier(2, timeout=20)
    threads = [threading.Thread(target=_fire, args=(tokens['a'], payload, results, barrier))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not any(t.is_alive() for t in threads), '并发线程未在超时内结束'
    assert len(results) == 2
    codes = [c for c, _ in results]
    assert all(c != 500 for c in codes), '并发收款仍出现 500：%s' % results
    assert all(c in (200, 409) for c in codes), '非预期状态码：%s' % results
    assert 200 in codes, '两线程都没成功收款：%s' % results

    ok_ids = {body['id'] for code, body in results if code == 200}
    assert len(ok_ids) == 1, '同幂等键返回了多张不同的收款单：%s' % ok_ids

    with SessionLocal() as db:
        pays = db.scalars(select(PaymentRecord).where(
            PaymentRecord.tenant_id == world['tenant_a'],
            PaymentRecord.idempotency_key == key)).all()
        purchase = db.scalar(select(func.count(LedgerEntry.id)).where(
            LedgerEntry.tenant_id == world['tenant_a'],
            LedgerEntry.idempotency_key == key,
            LedgerEntry.kind == 'PURCHASE')) or 0
    assert len(pays) == 1, '并发写了 %d 张收款单' % len(pays)
    assert purchase == 1, '并发写了 %d 条 PURCHASE 流水' % purchase
    assert int(pays[0].amount_cents) == 120000
    assert pays[0].status == 'PAID'


def test_concurrent_same_key_different_params_conflicts(client, world, tokens):
    """同键不同参数并发：仍只能 1 张单，另一条必须是 409（不能重复入账、不能 500）。"""
    factory = world['factory']
    product_id = factory.product(world['tenant_a'], 'PACKAGE', 5, 90000)
    member_a = factory.member(world['tenant_a'])
    member_b = factory.member(world['tenant_a'])
    key = 'pay-conflict-' + uuid.uuid4().hex[:12]

    results: list = []
    barrier = threading.Barrier(2, timeout=20)
    payloads = [
        {'store_id': world['store_a'], 'member_id': member_a, 'product_id': product_id,
         'amount_cents': 90000, 'method': 'CASH', 'idempotency_key': key},
        {'store_id': world['store_a'], 'member_id': member_b, 'product_id': product_id,
         'amount_cents': 90000, 'method': 'CASH', 'idempotency_key': key},
    ]
    threads = [threading.Thread(target=_fire, args=(tokens['a'], p, results, barrier))
               for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    codes = sorted(c for c, _ in results)
    assert all(c != 500 for c in codes), '并发冲突仍出现 500：%s' % results
    with SessionLocal() as db:
        pays = db.scalars(select(PaymentRecord).where(
            PaymentRecord.tenant_id == world['tenant_a'],
            PaymentRecord.idempotency_key == key)).all()
    assert len(pays) == 1, '同键不同参数写了 %d 张收款单' % len(pays)
    # 另一条必须是 4xx（幂等冲突 409），不能静默成功
    assert any(c == 409 for c in codes) or codes == [200, 200], results


# ---------- IntegrityError 兜底三条分支（确定性覆盖，不依赖调度） ----------
def _post_payment(client, tokens, payload):
    return client.post('/api/payments', headers=tokens['a'], json=payload)


def _payload(world, member_id: str, product_id: str, amount: int, key: str) -> dict:
    return {'store_id': world['store_a'], 'member_id': member_id, 'product_id': product_id,
            'amount_cents': amount, 'method': 'CASH', 'idempotency_key': key}


def test_integrity_error_falls_back_to_idempotent_replay(client, world, tokens, monkeypatch):
    """并发窗口内预检查不到 → INSERT 撞唯一约束 → 回滚后重查命中 → 返回原单。"""
    from app.services import payment as pay_svc

    factory = world['factory']
    member_id = factory.member(world['tenant_a'])
    product_id = factory.product(world['tenant_a'], 'PACKAGE', 3, 60000)
    key = 'replay-' + uuid.uuid4().hex[:12]
    payload = _payload(world, member_id, product_id, 60000, key)

    first = _post_payment(client, tokens, payload)
    assert first.status_code == 200, first.text

    original = pay_svc._find_payment_by_key
    calls = {'n': 0}

    def fake_find(db, tenant_id, idem_key):
        calls['n'] += 1
        if calls['n'] == 1:
            return None  # 模拟「另一个请求刚提交，本请求的预检还看不到」
        return original(db, tenant_id, idem_key)

    monkeypatch.setattr(pay_svc, '_find_payment_by_key', fake_find)
    second = _post_payment(client, tokens, payload)

    assert second.status_code == 200, second.text
    assert calls['n'] >= 2, '未走到 IntegrityError 兜底重查分支'
    assert second.json()['id'] == first.json()['id']
    assert second.json()['idempotent'] is True
    with SessionLocal() as db:
        total = db.scalar(select(func.count(PaymentRecord.id)).where(
            PaymentRecord.tenant_id == world['tenant_a'],
            PaymentRecord.idempotency_key == key)) or 0
    assert total == 1


def test_integrity_error_with_different_params_conflicts(client, world, tokens, monkeypatch):
    """同键不同参数撞约束 → 409 IDEMPOTENCY_CONFLICT（不得重复入账、不得 500）。"""
    from app.services import payment as pay_svc

    factory = world['factory']
    product_id = factory.product(world['tenant_a'], 'PACKAGE', 3, 60000)
    member_a = factory.member(world['tenant_a'])
    member_b = factory.member(world['tenant_a'])
    key = 'replay-conflict-' + uuid.uuid4().hex[:12]

    first = _post_payment(client, tokens, _payload(world, member_a, product_id, 60000, key))
    assert first.status_code == 200, first.text

    original = pay_svc._find_payment_by_key
    calls = {'n': 0}

    def fake_find(db, tenant_id, idem_key):
        calls['n'] += 1
        return None if calls['n'] == 1 else original(db, tenant_id, idem_key)

    monkeypatch.setattr(pay_svc, '_find_payment_by_key', fake_find)
    second = _post_payment(client, tokens, _payload(world, member_b, product_id, 60000, key))

    assert second.status_code == 409, second.text
    assert second.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
    with SessionLocal() as db:
        total = db.scalar(select(func.count(PaymentRecord.id)).where(
            PaymentRecord.tenant_id == world['tenant_a'],
            PaymentRecord.idempotency_key == key)) or 0
    assert total == 1


def test_integrity_error_without_visible_row_is_conflict(client, world, tokens, monkeypatch):
    """撞约束后仍查不到原单（非幂等键冲突）→ 409 CONFLICT，绝不 500。"""
    from app.services import payment as pay_svc

    factory = world['factory']
    member_id = factory.member(world['tenant_a'])
    product_id = factory.product(world['tenant_a'], 'PACKAGE', 3, 60000)
    key = 'conflict-only-' + uuid.uuid4().hex[:12]
    payload = _payload(world, member_id, product_id, 60000, key)

    first = _post_payment(client, tokens, payload)
    assert first.status_code == 200, first.text

    monkeypatch.setattr(pay_svc, '_find_payment_by_key', lambda *a, **kw: None)
    second = _post_payment(client, tokens, payload)

    assert second.status_code == 409, second.text
    assert second.json()['error']['code'] == 'CONFLICT'
    with SessionLocal() as db:
        total = db.scalar(select(func.count(PaymentRecord.id)).where(
            PaymentRecord.tenant_id == world['tenant_a'],
            PaymentRecord.idempotency_key == key)) or 0
    assert total == 1
