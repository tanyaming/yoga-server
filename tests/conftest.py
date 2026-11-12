"""pytest 公共夹具（T-13）：临时 SQLite（WAL）+ TestClient + 双租户。

硬性约束：
- **绝不读写仓库内 yoga.db**：数据库文件放在系统临时目录，会话结束整目录删除；
- 环境变量必须在导入 ``app`` 之前设置 —— ``app.config.settings`` 是导入期实例化的
  frozen dataclass，晚一步就锁死在默认 URL 上；
- ``APP_ENV=test`` 时 lifespan 不跑 ``init_db()``/``seed_demo()``，建表由本文件完成，
  保证测试数据完全可控。

数据准备策略：租户/门店/账号/会员/商品只在会话级 fixture 里建一次；
需要"每个用例都是干净的"的实体（课节、收款单）由 ``Factory`` 现造现用，
ID 全部走 uuid，互不干扰。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from datetime import timedelta

_TMP_DIR = tempfile.mkdtemp(prefix='yoga-pytest-')

os.environ['APP_ENV'] = 'test'
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TMP_DIR, 'test.db')
os.environ['JWT_SECRET'] = 'pytest-secret-key-please-do-not-use-in-prod'
os.environ['SEED_DEMO'] = 'false'
os.environ['WX_MOCK'] = 'true'
os.environ['WX_SUBSCRIBE_ENABLED'] = 'false'
os.environ['ATTENDANCE_WINDOW_MINUTES'] = '0'
os.environ['LOG_LEVEL'] = 'WARNING'
os.environ['BOOTSTRAP_ADMIN_PASSWORD'] = 'admin12345'

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (Base, CourseTemplate, Entitlement, Member, Product, Store,  # noqa: E402
                        Tenant, User)
from app.security import hash_password, mask_phone, phone_hash, random_id  # noqa: E402
from app.utils.timeutil import now_naive  # noqa: E402

ADMIN_PASSWORD = 'pytest-admin-1234'
TENANT_A = 't-a'
TENANT_B = 't-b'
STORE_A = 'store-a'
STORE_B = 'store-b'


# ---------- 建表与销毁 ----------
@pytest.fixture(scope='session', autouse=True)
def _schema():
    """会话级建表；结束后释放连接并删除临时库目录。"""
    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


@pytest.fixture(scope='session')
def client(_schema):
    """带 lifespan 的 TestClient（会跑配置校验，不建表、不种演示数据）。"""
    with TestClient(app) as c:
        yield c


# ---------- 数据工厂 ----------
class Factory:
    """按需造业务实体的工厂：每次调用都独立开会话并提交，避免与会话事务纠缠。"""

    def member(self, tenant_id: str, name: str = '') -> str:
        member_id = random_id()
        phone = '139%08d' % (uuid.uuid4().int % 100000000)
        with SessionLocal() as db:
            db.add(Member(id=member_id, tenant_id=tenant_id, phone_hash=phone_hash(phone),
                          phone_mask=mask_phone(phone), name=name or ('会员' + member_id[:6])))
            db.commit()
        return member_id

    def product(self, tenant_id: str, ptype: str = 'PACKAGE', total_sessions: int = 4,
                price_cents: int = 199900) -> str:
        product_id = random_id()
        with SessionLocal() as db:
            db.add(Product(id=product_id, tenant_id=tenant_id,
                           code='P' + product_id[:8].upper(), name='测试商品' + product_id[:4],
                           ptype=ptype, total_sessions=total_sessions,
                           price_cents=price_cents, valid_days=365, cross_store=True))
            db.commit()
        return product_id

    def template(self, tenant_id: str, store_id: str, capacity: int = 8,
                 coach_id: str = '') -> str:
        template_id = random_id()
        with SessionLocal() as db:
            db.add(CourseTemplate(id=template_id, tenant_id=tenant_id,
                                  code='C' + template_id[:8].upper(),
                                  name='测试课程' + template_id[:4], store_id=store_id,
                                  coach_id=coach_id, ctype='GROUP', capacity=capacity,
                                  duration_minutes=60))
            db.commit()
        return template_id

    def session(self, tenant_id: str, store_id: str = STORE_A, capacity: int = 1,
                hours_ahead: int = 48, template_id: str = '', course_name: str = '测试团课') -> str:
        session_id = random_id()
        starts = (now_naive() + timedelta(hours=hours_ahead)).replace(microsecond=0)
        with SessionLocal() as db:
            from app.models import ClassSession
            db.add(ClassSession(
                id=session_id, tenant_id=tenant_id, template_id=template_id or 'tpl-test',
                store_id=store_id, coach_id='', course_name=course_name,
                starts_at=starts, ends_at=starts + timedelta(minutes=60),
                capacity=capacity, booked=0, status='OPEN', cancel_deadline_hours=2))
            db.commit()
        return session_id

    def entitlement(self, tenant_id: str, member_id: str, total_sessions: int = 10,
                    price_cents: int = 199900, ptype: str = 'PACKAGE',
                    used_sessions: int = 0, balance_cents: int = 0,
                    payment_id: str = '') -> str:
        entitlement_id = random_id()
        with SessionLocal() as db:
            db.add(Entitlement(
                id=entitlement_id, tenant_id=tenant_id, member_id=member_id,
                product_id='prod-test', product_name='测试权益', ptype=ptype,
                total_sessions=total_sessions, used_sessions=used_sessions,
                price_cents=price_cents, balance_cents=balance_cents,
                expires_at=now_naive() + timedelta(days=365), status='ACTIVE',
                source_payment_id=payment_id, cross_store=True))
            db.commit()
        return entitlement_id


@pytest.fixture(scope='session')
def world(_schema):
    """基础数据集：双租户 + 门店 + 账号 + 会员。返回 ids 与工厂。"""
    with SessionLocal() as db:
        for tid, code, name in ((TENANT_A, 'TA', '测试租户A'), (TENANT_B, 'TB', '测试租户B')):
            if db.get(Tenant, tid) is None:
                db.add(Tenant(id=tid, code=code, name=name))
        db.flush()
        for sid, tid, code, name in ((STORE_A, TENANT_A, 'SA', 'A店主店'),
                                     (STORE_B, TENANT_B, 'SB', 'B店主店')):
            if db.get(Store, sid) is None:
                db.add(Store(id=sid, tenant_id=tid, code=code, name=name,
                             address='', phone_mask='', business_hours='09:00-21:00'))
        db.flush()
        accounts = (
            ('user-admin-a', TENANT_A, 'admin_a', 'TENANT_ADMIN', STORE_A),
            ('user-admin-b', TENANT_B, 'admin_b', 'TENANT_ADMIN', STORE_B),
            ('user-coach-a', TENANT_A, 'coach_a', 'COACH', STORE_A),
        )
        for uid, tid, username, role, store_id in accounts:
            if db.get(User, uid) is None:
                db.add(User(id=uid, tenant_id=tid, username=username,
                            password_hash=hash_password(ADMIN_PASSWORD),
                            real_name=username, role=role, store_id=store_id))
        db.commit()

    factory = Factory()
    return {
        'tenant_a': TENANT_A, 'tenant_b': TENANT_B,
        'store_a': STORE_A, 'store_b': STORE_B,
        'factory': factory,
        'members': [factory.member(TENANT_A) for _ in range(3)],
    }


def _login(client: TestClient, username: str, tenant_id: str) -> dict:
    resp = client.post('/api/auth/login',
                       json={'username': username, 'password': ADMIN_PASSWORD,
                             'tenant_id': tenant_id})
    assert resp.status_code == 200, '登录失败：%s' % resp.text
    return {'Authorization': 'Bearer ' + resp.json()['access_token']}


@pytest.fixture(scope='session')
def tokens(client, world):
    """后台账号令牌：a=A 租户管理员，b=B 租户管理员，coach_a=教练。"""
    return {
        'a': _login(client, 'admin_a', TENANT_A),
        'b': _login(client, 'admin_b', TENANT_B),
        'coach_a': _login(client, 'coach_a', TENANT_A),
    }


@pytest.fixture
def member_token(client, world):
    """会员端令牌：走 wx-bind 真实链路（WX_MOCK=true），返回 (member_id, headers)。"""
    def _mk(tenant_id: str = TENANT_A, name: str = '会员端测试') -> tuple:
        code = 'code-' + uuid.uuid4().hex[:12]
        phone = '138%08d' % (uuid.uuid4().int % 100000000)
        resp = client.post('/api/auth/wx-bind', json={
            'code': code, 'tenant_id': tenant_id, 'phone': phone, 'name': name})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        return body['member']['id'], {'Authorization': 'Bearer ' + body['token']}
    return _mk
