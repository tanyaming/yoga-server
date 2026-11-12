"""并发约课回归（T-05）：SQLite 屏障 / 原子占位 / 名额口径 / 候补序号。

这些用例是用来「锁住修复」的：任何把占位逻辑改回读-改-写、
或用 with_for_update()（SQLite 静默 no-op）的实现都会让它们变红。
"""
from __future__ import annotations

import threading
import uuid

from sqlalchemy import func, select, text, update

from app.database import SessionLocal, engine
from app.errors import DuplicateBooking
from app.models import ClassSession, Enrollment
from app.services import schedule as sch
from app.utils.timeutil import now_naive

from datetime import timedelta

SEAT_STATUSES = ('BOOKED', 'OFFERED')


def _active_count(db, session_id: str) -> int:
    db.expire_all()
    return int(db.scalar(select(func.count(Enrollment.id)).where(
        Enrollment.session_id == session_id,
        Enrollment.status.in_(SEAT_STATUSES))) or 0)


def _snapshot(session_id: str) -> tuple:
    """返回 (booked, 实际占位人数, capacity)，并强制重读，避免 identity map 缓存。"""
    with SessionLocal() as db:
        db.expire_all()
        s = db.get(ClassSession, session_id)
        assert s is not None
        return int(s.booked), _active_count(db, session_id), int(s.capacity)


def _assert_no_drift(session_id: str) -> None:
    booked, active, capacity = _snapshot(session_id)
    assert booked == active, '名额口径漂移：booked=%d 但实际占位=%d' % (booked, active)
    assert 0 <= booked <= capacity, '名额越界：booked=%d capacity=%d' % (booked, capacity)


def _enrollment(session_id: str, member_id: str):
    with SessionLocal() as db:
        db.expire_all()
        return db.scalar(select(Enrollment).where(
            Enrollment.session_id == session_id, Enrollment.member_id == member_id))


def _expire_offer_now(session_id: str) -> None:
    """把当前 OFFERED 的锁定窗口推到过去，模拟「会员没在 15 分钟内确认」。"""
    with SessionLocal() as db:
        db.execute(update(Enrollment)
                   .where(Enrollment.session_id == session_id,
                          Enrollment.status == 'OFFERED')
                   .values(offer_expires_at=now_naive() - timedelta(minutes=1))
                   .execution_options(synchronize_session=False))
        db.commit()


def _book(member_id: str, session_id: str, tenant_id: str = 't-a') -> dict:
    with SessionLocal() as db:
        result = sch.book(db, tenant_id, member_id, session_id, 'MEMBER')
        db.commit()
        return result


# ---------- T-01 SQLite 并发屏障 ----------
def test_sqlite_no_lock():
    """WAL + busy_timeout 生效：并发写不再立刻 database is locked。"""
    with engine.connect() as conn:
        journal = str(conn.execute(text('PRAGMA journal_mode')).scalar() or '').lower()
        busy = int(conn.execute(text('PRAGMA busy_timeout')).scalar() or 0)
        fk = int(conn.execute(text('PRAGMA foreign_keys')).scalar() or 0)
    assert journal == 'wal'
    assert busy >= 8000
    assert fk == 1  # 原有外键约束行为不能被覆盖掉


# ---------- T-02 原子占位 ----------
def test_capacity_one_5threads(world):
    """容量=1、5 会员并发（barrier 同步起跑 + 各自独立 Session）→ 1 BOOKED + 4 WAITLIST。"""
    factory = world['factory']
    session_id = factory.session('t-a', capacity=1, hours_ahead=72)
    members = [factory.member('t-a') for _ in range(5)]
    for mid in members:
        factory.entitlement('t-a', mid, total_sessions=10, price_cents=199900)

    barrier = threading.Barrier(len(members), timeout=30)
    results: list = []
    errors: list = []
    lock = threading.Lock()

    def worker(member_id: str) -> None:
        db = SessionLocal()
        try:
            barrier.wait()
            out = sch.book(db, 't-a', member_id, session_id, 'MEMBER')
            db.commit()
            with lock:
                results.append(out)
        except Exception as exc:  # noqa: BLE001 - 汇总后统一断言，避免线程里吞掉异常
            with lock:
                errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=(mid,)) for mid in members]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not any(t.is_alive() for t in threads), '并发线程未在超时内结束'
    assert not errors, '并发约课抛出异常：%s' % [repr(e) for e in errors]

    booked = [r for r in results if r['status'] == 'BOOKED']
    waitlist = [r for r in results if r['status'] == 'WAITLIST']
    assert len(booked) == 1, '超卖：%d 个 BOOKED' % len(booked)
    assert len(waitlist) == 4

    booked_db, active, _ = _snapshot(session_id)
    assert booked_db == 1
    assert active == 1
    with SessionLocal() as db:
        status = db.get(ClassSession, session_id).status
    assert status == 'FULL'

    # 候补序号单语句取号，不允许并列
    seqs = sorted(r['waitlist_seq'] for r in waitlist)
    assert seqs == [1, 2, 3, 4], '候补序号并列或跳号：%s' % seqs


def test_duplicate_member_race_is_conflict(world):
    """同会员并发重复约 → DuplicateBooking(409)，不能漏成 IntegrityError/500。"""
    factory = world['factory']
    session_id = factory.session('t-a', capacity=5, hours_ahead=96)
    member_id = factory.member('t-a')
    factory.entitlement('t-a', member_id, total_sessions=10)

    _book(member_id, session_id)
    try:
        _book(member_id, session_id)
    except DuplicateBooking as exc:
        assert exc.http_status == 409
    else:  # pragma: no cover - 未抛异常说明重复约课没被拦住
        raise AssertionError('重复约课未被拦截')
    _assert_no_drift(session_id)


# ---------- T-03 名额口径零漂移 ----------
def test_booked_drift(world):
    """20 次「取消 → 递补 → 超时顺延 → 确认 → 再取消」循环后 booked 与 COUNT 零漂移。"""
    factory = world['factory']
    m1, m2, m3 = [factory.member('t-a') for _ in range(3)]
    for mid in (m1, m2, m3):
        factory.entitlement('t-a', mid, total_sessions=50)

    for cycle in range(20):
        session_id = factory.session('t-a', capacity=1, hours_ahead=72 + cycle)
        _book(m1, session_id)
        _book(m2, session_id)
        _book(m3, session_id)
        _assert_no_drift(session_id)
        assert _snapshot(session_id)[0] == 1

        # ① m1 取消 → m2 自动递补 OFFERED（名额仍被占住）
        e1 = _enrollment(session_id, m1)
        with SessionLocal() as db:
            sch.cancel_booking(db, 't-a', m1, e1.id)
            db.commit()
        _assert_no_drift(session_id)
        e2 = _enrollment(session_id, m2)
        assert e2.status == 'OFFERED', '第 %d 轮取消后未递补' % cycle

        # ② m2 超出 15 分钟未确认 → 退回队尾，m3 顺延
        _expire_offer_now(session_id)
        with SessionLocal() as db:
            sch.expire_offers(db, 't-a', session_id)
            db.commit()
        _assert_no_drift(session_id)
        e3 = _enrollment(session_id, m3)
        assert e3.status == 'OFFERED', '第 %d 轮超时后未顺延' % cycle
        assert _enrollment(session_id, m2).status == 'WAITLIST'

        # ③ m3 确认 → BOOKED；名额不变
        with SessionLocal() as db:
            sch.confirm_offer(db, 't-a', m3, e3.id)
            db.commit()
        _assert_no_drift(session_id)
        assert _snapshot(session_id)[0] == 1

        # ④ m3 取消 → 名额顺延回 m2（此时只有他还在候补队列）
        with SessionLocal() as db:
            sch.cancel_booking(db, 't-a', m3, e3.id)
            db.commit()
        _assert_no_drift(session_id)
        assert _snapshot(session_id)[0] == 1
        assert _enrollment(session_id, m2).status == 'OFFERED'

        # ⑤ m2 也超时 → 退回队尾；队列里只剩他，名额不能空转，于是重新递补（新 15 分钟窗口）
        _expire_offer_now(session_id)
        with SessionLocal() as db:
            sch.expire_offers(db, 't-a', session_id)
            db.commit()
        _assert_no_drift(session_id)
        assert _snapshot(session_id)[0] == 1

        # ⑥ m2 放弃名额 → 队列彻底清空，booked 归零
        e2 = _enrollment(session_id, m2)
        with SessionLocal() as db:
            sch.cancel_booking(db, 't-a', m2, e2.id)
            db.commit()
        _assert_no_drift(session_id)
        assert _snapshot(session_id)[0] == 0, '第 %d 轮收尾 booked 未归零' % cycle


def test_recount_is_idempotent(world):
    """recount 反复执行结果稳定，且能把人为制造的 booked 漂移修回真实值。"""
    factory = world['factory']
    session_id = factory.session('t-a', capacity=3, hours_ahead=120)
    m = factory.member('t-a')
    factory.entitlement('t-a', m, total_sessions=10)
    _book(m, session_id)

    with SessionLocal() as db:
        # 模拟历史脏数据：booked 被写高
        db.execute(update(ClassSession).where(ClassSession.id == session_id)
                   .values(booked=3).execution_options(synchronize_session=False))
        db.commit()

    with SessionLocal() as db:
        for _ in range(3):
            assert sch.recount_session_booked(db, 't-a', session_id) == 1
            db.commit()
    _assert_no_drift(session_id)


def test_waitlist_seq_starts_at_one_and_is_unique(world):
    """候补序号从 1 递增且唯一（单语句 MAX+1 取号）。"""
    factory = world['factory']
    session_id = factory.session('t-a', capacity=1, hours_ahead=130)
    members = [factory.member('t-a') for _ in range(4)]
    for mid in members:
        factory.entitlement('t-a', mid, total_sessions=10)

    out = [_book(mid, session_id) for mid in members]
    assert out[0]['status'] == 'BOOKED'
    seqs = [r['waitlist_seq'] for r in out[1:]]
    assert seqs == [1, 2, 3]
    assert len(set(seqs)) == len(seqs)


def test_check_constraint_blocks_overbooked_write(world):
    """库级 CHECK 兜底：绕过服务直接写超卖会被拒绝。"""
    factory = world['factory']
    session_id = factory.session('t-a', capacity=1, hours_ahead=140)
    error = None
    with SessionLocal() as db:
        try:
            db.execute(update(ClassSession).where(ClassSession.id == session_id)
                       .values(booked=2).execution_options(synchronize_session=False))
            db.commit()
        except Exception as exc:  # noqa: BLE001 - 只关心「被拒绝」这一事实
            error = exc
            db.rollback()
    assert error is not None, 'CHECK 未生效：超卖写入被允许了'


# ---------- 取消后重约同一节课（复用 CANCELLED 记录） ----------
def test_rebook_after_cancel(world):
    """约课 → 取消 → 再约同一节：必须成功，且名额回到 1（不能撞全状态唯一约束）。"""
    factory = world['factory']
    member_id = factory.member('t-a')
    factory.entitlement('t-a', member_id, total_sessions=10)
    session_id = factory.session('t-a', capacity=1, hours_ahead=150)

    first = _book(member_id, session_id)
    assert first['status'] == 'BOOKED'
    with SessionLocal() as db:
        sch.cancel_booking(db, 't-a', member_id, first['id'])
        db.commit()
    _assert_no_drift(session_id)
    assert _snapshot(session_id)[0] == 0

    # 复用的是同一行，不新插入；booked 重新回到 1
    again = _book(member_id, session_id)
    assert again['status'] == 'BOOKED'
    assert again['id'] == first['id'], '取消记录应被复用而不是新建'
    _assert_no_drift(session_id)
    assert _snapshot(session_id)[0] == 1

    with SessionLocal() as db:
        db.expire_all()
        row = db.get(Enrollment, again['id'])
        assert row.status == 'BOOKED'
        assert row.cancelled_at is None
        assert row.offer_expires_at is None
        total = db.scalar(select(func.count(Enrollment.id)).where(
            Enrollment.session_id == session_id, Enrollment.member_id == member_id))
    assert total == 1, '同一会员在同一课节不允许出现两条记录'


def test_rebook_after_cancel_full_session_goes_waitlist(world):
    """取消后再约时若名额被别人占了，复用记录进候补并拿到新序号。"""
    factory = world['factory']
    m1 = factory.member('t-a')
    m2 = factory.member('t-a')
    for mid in (m1, m2):
        factory.entitlement('t-a', mid, total_sessions=10)
    session_id = factory.session('t-a', capacity=1, hours_ahead=160)

    first = _book(m1, session_id)
    with SessionLocal() as db:
        sch.cancel_booking(db, 't-a', m1, first['id'])
        db.commit()
    assert _book(m2, session_id)['status'] == 'BOOKED'

    again = _book(m1, session_id)
    assert again['id'] == first['id']
    assert again['status'] == 'WAITLIST'
    assert again['waitlist_seq'] == 1
    _assert_no_drift(session_id)
    assert _snapshot(session_id)[0] == 1


def test_rebook_after_cancel_over_http(client, member_token, tokens, world):
    """HTTP 全链路：会员端约课 → 取消 → 再约同一节，全程不得出现 409。"""
    token_member_id, headers = member_token()
    world['factory'].entitlement('t-a', token_member_id, total_sessions=10)
    session_id = world['factory'].session('t-a', capacity=2, hours_ahead=170)

    booked = client.post('/api/app/book', headers=headers, json={'session_id': session_id})
    assert booked.status_code == 200, booked.text
    enrollment_id = booked.json()['id']
    assert booked.json()['status'] == 'BOOKED'

    cancelled = client.post('/api/app/cancel', headers=headers,
                            json={'enrollment_id': enrollment_id})
    assert cancelled.status_code == 200, cancelled.text

    rebooked = client.post('/api/app/book', headers=headers, json={'session_id': session_id})
    assert rebooked.status_code == 200, rebooked.text
    assert rebooked.json()['status'] == 'BOOKED'
    assert rebooked.json()['id'] == enrollment_id, '取消记录应被复用，而不是撞唯一约束'

    # 后台视角回显的 booked 已刷新（identity map 不能是旧值）
    detail = client.get('/api/schedule/sessions/%s' % session_id, headers=tokens['a'])
    assert detail.status_code == 200, detail.text
    assert detail.json()['booked'] == 1
    assert _snapshot(session_id)[0] == 1


# ---------- 点名后名额回落（T-03 口径补口） ----------
def test_attendance_recounts_booked(world):
    """容量 2 约满 → 点名 1 人 ATTENDED → booked==1 且 OPEN；再点名 → booked==0。"""
    from app.services import attendance as att_svc

    factory = world['factory']
    session_id = factory.session('t-a', capacity=2, hours_ahead=180)
    members = [factory.member('t-a') for _ in range(2)]
    for mid in members:
        factory.entitlement('t-a', mid, total_sessions=5)
    for mid in members:
        assert _book(mid, session_id)['status'] == 'BOOKED'
    assert _snapshot(session_id)[:1] == (2,)
    with SessionLocal() as db:
        assert db.get(ClassSession, session_id).status == 'FULL'

    operator = {'id': 'user-coach-a', 'name': 'coach_a'}
    for index, mid in enumerate(members, start=1):
        with SessionLocal() as db:
            att_svc.mark_attendance(db, 't-a', operator, session_id, mid, 'ATTENDED')
            db.commit()
        booked, active, _ = _snapshot(session_id)
        assert booked == 2 - index, '点名第 %d 人后 booked 未回落' % index
        assert active == booked, 'booked 与 COUNT(BOOKED,OFFERED) 不一致'
        with SessionLocal() as db:
            status = db.get(ClassSession, session_id).status
        assert status == 'OPEN', '有人退出占位后课节应回到 OPEN'

    assert _snapshot(session_id)[0] == 0


def test_attendance_noshow_recounts_booked(world):
    """爽约点名同样让会员退出占位，booked 必须回落。"""
    from app.services import attendance as att_svc

    factory = world['factory']
    session_id = factory.session('t-a', capacity=1, hours_ahead=190)
    member_id = factory.member('t-a')
    factory.entitlement('t-a', member_id, total_sessions=5)
    assert _book(member_id, session_id)['status'] == 'BOOKED'

    with SessionLocal() as db:
        att_svc.mark_attendance(db, 't-a', {'id': 'user-coach-a', 'name': 'coach_a'},
                                session_id, member_id, 'NO_SHOW')
        db.commit()
    _assert_no_drift(session_id)
    assert _snapshot(session_id)[0] == 0
