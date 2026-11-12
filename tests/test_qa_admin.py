"""QA 独立验收（严过关）：后台自助能力（门店/账号/模板/按周排课）+ 权限面变更。

独立原则：
- 不复用工程师用例的断言与数据：每个用例用 uuid 现造门店/账号/模板/课节；
- 场景 A（空库开一家店）在**独立进程**里跑 `/tmp/qa-admin.db` 全新库，
  表结构一律由 `alembic upgrade head` 建立（不是 metadata.create_all），
  且不使用 seed 的演示业务数据；
- 只读校验前端（不执行浏览器），守卫是否"先判断后请求"用源码顺序核对。

运行：``.venv/bin/python -m pytest tests/test_qa_admin.py -q``
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from app.database import SessionLocal
from app.models import User
from app.security import hash_password

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_JS = REPO_ROOT / 'web' / 'assets' / 'app.js'
WEB_CSS = REPO_ROOT / 'web' / 'assets' / 'app.css'
WEB_HTML = REPO_ROOT / 'web' / 'index.html'

QA_PASSWORD = 'qa-selfsvc-8f2c'
FRESH_DB = '/tmp/qa-admin.db'


# ======================= 通用工具（独立造数据） =======================
def _uid(prefix: str = 'qa') -> str:
    return prefix + '-' + uuid.uuid4().hex[:8]


def _login(client, username: str, password: str = QA_PASSWORD) -> dict:
    """账号密码登录，返回 Authorization 头。"""
    resp = client.post('/api/auth/login', json={'username': username, 'password': password})
    assert resp.status_code == 200, '登录失败：%s %s' % (resp.status_code, resp.text)
    return {'Authorization': 'Bearer ' + resp.json()['access_token']}


def _mk_store(client, h: dict, name: str = '') -> dict:
    resp = client.post('/api/tenant/stores', headers=h, json={
        'name': name or ('QA门店' + uuid.uuid4().hex[:4]),
        'code': 'Q' + uuid.uuid4().hex[:7].upper(),
        'address': '测试路 1 号', 'phone_mask': '010-****0000',
        'business_hours': '09:00-21:00'})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_user(client, h: dict, role: str, store_id: str = '', username: str = '') -> dict:
    resp = client.post('/api/tenant/users', headers=h, json={
        'username': username or _uid('u'), 'password': QA_PASSWORD,
        'real_name': 'QA' + role[:4], 'role': role, 'store_id': store_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_template(client, h: dict, store_id: str = '', **kw) -> dict:
    body = {'name': 'QA课程' + uuid.uuid4().hex[:4], 'store_id': store_id,
            'coach_id': kw.pop('coach_id', ''), 'ctype': 'GROUP',
            'capacity': 8, 'duration_minutes': 60, 'allowed_ptypes': 'PACKAGE'}
    body.update(kw)
    resp = client.post('/api/schedule/templates', headers=h, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_session(client, h: dict, template_id: str, starts_at: str) -> dict:
    resp = client.post('/api/schedule/sessions', headers=h,
                       json={'template_id': template_id, 'starts_at': starts_at,
                             'cancel_deadline_hours': 2})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _sessions(client, h: dict, **params) -> list:
    query = '&'.join('%s=%s' % (k, v) for k, v in params.items())
    resp = client.get('/api/schedule/sessions' + ('?' + query if query else ''), headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _count_by_tpl(client, h: dict, course_name: str) -> int:
    return len([s for s in _sessions(client, h) if s['course_name'] == course_name])


# ======================= A. 空库自助（独立进程 + alembic） =======================
_FLOW_SRC = r'''
"""空库「30 分钟开一家店」全流程；只允许开户阶段写 1 个租户 + 1 个馆主。"""
import json
import sys
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Tenant, User
from app.security import hash_password

OUT, STATUSES, NOTES = [], [], []


def rec(name, ok, detail=''):
    OUT.append({'name': name, 'ok': bool(ok), 'detail': str(detail)[:300]})


def note(name, detail):
    NOTES.append({'name': name, 'detail': str(detail)[:300]})


def call(c, method, path, **kw):
    r = c.request(method, path, **kw)
    STATUSES.append([method, path, r.status_code])
    return r


with SessionLocal() as db:                       # 开户：仅此两行直接写库
    db.add(Tenant(id='tenant-qa', code='QA', name='QA 空库新店'))
    db.add(User(id='user-qa-owner', tenant_id='tenant-qa', username='owner',
                password_hash=hash_password('qa-owner-1234'), real_name='馆主',
                role='TENANT_ADMIN'))
    db.commit()

with TestClient(app) as c:
    r = call(c, 'POST', '/api/auth/login',
             json={'username': 'owner', 'password': 'qa-owner-1234'})
    rec('A0 空库馆主可登录', r.status_code == 200, r.text)
    H = {'Authorization': 'Bearer ' + r.json()['access_token']}

    r = call(c, 'GET', '/api/auth/me', headers=H)
    rec('A0 /api/auth/me 回显馆主角色', r.status_code == 200
        and r.json().get('role') == 'TENANT_ADMIN', r.text)

    # 1. 两家门店
    stores = []
    for name, code in (('望京店', 'WJ'), ('三里屯店', 'SLT')):
        r = call(c, 'POST', '/api/tenant/stores', headers=H,
                 json={'name': name, 'code': code, 'address': name + '路 1 号',
                       'phone_mask': '010-****8888', 'business_hours': '09:00-21:00'})
        rec('A1 新建门店 %s' % name, r.status_code == 200, r.text)
        stores.append(r.json()['id'])
    r = call(c, 'GET', '/api/tenant/stores', headers=H)
    rec('A1 门店列表 = 2', r.status_code == 200 and len(r.json()) == 2,
        [s['name'] for s in r.json()])
    r = call(c, 'PUT', '/api/tenant/stores/' + stores[0], headers=H,
             json={'address': '望京 SOHO T1', 'business_hours': '08:00-22:00'})
    rec('A1 PUT 改门店地址生效', r.status_code == 200
        and r.json().get('address') == '望京 SOHO T1', r.text)

    # 2. 三个账号（店长/教练/前台）——仓库无「前台」角色，前台以 STORE_MANAGER 代替
    users = {}
    for uname, real, role, sid in (('zhangdian', '张店长', 'STORE_MANAGER', stores[0]),
                                   ('liCoach', '李教练', 'COACH', stores[0]),
                                   ('wangqd', '王前台', 'STORE_MANAGER', stores[1])):
        r = call(c, 'POST', '/api/tenant/users', headers=H,
                 json={'username': uname, 'password': 'qa-pass-1234', 'real_name': real,
                       'role': role, 'store_id': sid})
        rec('A2 新建账号 %s(%s)' % (uname, role), r.status_code == 200, r.text)
        users[uname] = r.json()['id']
    note('交付说明「前台」账号', '代码只支持 PLATFORM_ADMIN/TENANT_ADMIN/STORE_MANAGER/COACH，'
         '无 FRONT_DESK/前台角色；本用例以 STORE_MANAGER 代替，需产品确认口径')
    r = call(c, 'GET', '/api/tenant/users', headers=H)
    rec('A2 账号列表含馆主共 4 条', r.status_code == 200 and len(r.json()) == 4, len(r.json()))
    r = call(c, 'GET', '/api/tenant/coaches', headers=H)
    rec('A2 教练字典只有 1 人', r.status_code == 200 and len(r.json()) == 1, r.text)

    # 3. 两个模板（容量/时长/卡种不同）
    r = call(c, 'POST', '/api/schedule/templates', headers=H,
             json={'name': '流瑜伽 60', 'store_id': stores[0], 'coach_id': users['liCoach'],
                   'ctype': 'GROUP', 'capacity': 12, 'duration_minutes': 60,
                   'allowed_ptypes': 'PACKAGE'})
    rec('A3 模板1 容量12/60min/PACKAGE', r.status_code == 200, r.text)
    tpl1 = r.json()['id']
    r = call(c, 'POST', '/api/schedule/templates', headers=H,
             json={'name': '普拉提私教 90', 'store_id': stores[1], 'coach_id': '',
                   'ctype': 'PRIVATE', 'capacity': 1, 'duration_minutes': 90,
                   'allowed_ptypes': 'PRIVATE,STORED'})
    rec('A3 模板2 容量1/90min/PRIVATE,STORED', r.status_code == 200, r.text)
    r = call(c, 'GET', '/api/schedule/templates', headers=H)
    rec('A3 模板列表 2 条且卡种规范化',
        r.status_code == 200 and len(r.json()) == 2
        and sorted(t['allowed_ptypes'] for t in r.json()) == ['PACKAGE', 'PRIVATE,STORED'],
        [(t['name'], t['allowed_ptypes']) for t in r.json()])

    # 4. 按周生成（2 个时段）
    today = datetime.now()
    week_start = (today + timedelta(days=(7 - today.weekday()))).strftime('%Y-%m-%d')
    times = ['09:00', '19:30']
    payload = {'template_id': tpl1, 'week_start': week_start, 'times': times,
               'cancel_deadline_hours': 2}
    r = call(c, 'POST', '/api/schedule/weekly', headers=H, json=payload)
    body = r.json()
    rec('A4 按周生成 200', r.status_code == 200, r.text)
    expect = 7 * len(set(times))
    rec('A4 created==7天×2时段=14', body.get('created') == expect, body)
    if body.get('created') != 6:
        note('交付说明「下周 3 天 × 2 时段 = 6 节」',
             '接口无「选择周几」参数，固定生成 7 天；实测 created=%s。'
             '馆主无法只排周一三五，只能排满一周再逐节取消' % body.get('created'))
    rows = [s for s in call(c, 'GET', '/api/schedule/sessions', headers=H).json()
            if s['course_name'] == '流瑜伽 60']
    rec('A4 落库 14 节且容量为模板快照 12',
        len(rows) == 14 and {s['capacity'] for s in rows} == {12},
        (len(rows), {s['capacity'] for s in rows}))

    # 5. 重复生成 3 次
    for i in range(3):
        r = call(c, 'POST', '/api/schedule/weekly', headers=H, json=payload)
        b = r.json()
        rec('A5 第%d次重复生成 created==0' % (i + 2),
            r.status_code == 200 and b.get('created') == 0, b)
        rec('A5 第%d次 skipped==14' % (i + 2), b.get('skipped') == 14, b)
    rows = [s for s in call(c, 'GET', '/api/schedule/sessions', headers=H).json()
            if s['course_name'] == '流瑜伽 60']
    rec('A5 总课节数仍为 14（无重复）', len(rows) == 14, len(rows))
    rec('A5 开始时间唯一', len({s['starts_at'] for s in rows}) == len(rows), len(rows))

    # 6. 改模板容量
    r = call(c, 'PATCH', '/api/schedule/templates/' + tpl1, headers=H,
             json={'capacity': 20})
    b = r.json()
    rec('A6 PATCH 模板 200', r.status_code == 200, r.text)
    rec('A6 affected_future_sessions==14', b.get('affected_future_sessions') == 14,
        b.get('affected_future_sessions'))
    if b.get('affected_future_sessions') != 6:
        note('交付说明「affected_future_sessions==6」',
             '实际 %s（因为按周生成固定 7 天，未来课节本就是 14 节）'
             % b.get('affected_future_sessions'))
    rows = [s for s in call(c, 'GET', '/api/schedule/sessions', headers=H).json()
            if s['course_name'] == '流瑜伽 60']
    rec('A6 存量 14 节容量保持 12（快照不回写）',
        len(rows) == 14 and {s['capacity'] for s in rows} == {12},
        {s['capacity'] for s in rows})

    new_start = (datetime.strptime(week_start, '%Y-%m-%d')
                 + timedelta(days=9, hours=10)).strftime('%Y-%m-%d %H:%M')
    r = call(c, 'POST', '/api/schedule/sessions', headers=H,
             json={'template_id': tpl1, 'starts_at': new_start,
                   'cancel_deadline_hours': 2})
    rec('A6 新建 1 节课 200', r.status_code == 200, r.text)
    fresh = [s for s in call(c, 'GET', '/api/schedule/sessions', headers=H).json()
             if s['starts_at'] == new_start]
    rec('A6 新课节容量=新值 20',
        len(fresh) == 1 and fresh[0]['capacity'] == 20, fresh)

    # 7. 教练登录拉自己的课
    r = call(c, 'POST', '/api/auth/login',
             json={'username': 'liCoach', 'password': 'qa-pass-1234'})
    rec('A7 教练账号可登录', r.status_code == 200, r.text)
    HC = {'Authorization': 'Bearer ' + r.json()['access_token']}
    r = call(c, 'GET', '/api/schedule/sessions?coach_id=' + users['liCoach'], headers=HC)
    mine = r.json() if r.status_code == 200 else []
    rec('A7 教练拉到自己名下 15 节课',
        r.status_code == 200 and len(mine) == 15
        and {s['coach_id'] for s in mine} == {users['liCoach']},
        (r.status_code, len(mine)))
    r = call(c, 'GET', '/api/coach/today', headers=HC)
    rec('A7 /api/coach/today 200', r.status_code == 200, r.text)

    # C（同库顺手）：停用门店必须先结清未来课
    r = call(c, 'POST', '/api/tenant/stores/' + stores[0] + '/disable', headers=H)
    msg = r.json().get('error', {}).get('message', '') if r.headers.get(
        'content-type', '').startswith('application/json') else r.text
    rec('C1 有未来课节停用门店 422 且文案含节数',
        r.status_code == 422 and '15' in msg, [r.status_code, msg])
    for sid in [s['id'] for s in call(
            c, 'GET', '/api/schedule/sessions', headers=H).json()
            if s['course_name'] == '流瑜伽 60']:
        call(c, 'POST', '/api/schedule/sessions/%s/cancel' % sid, headers=H)
    r = call(c, 'POST', '/api/tenant/stores/' + stores[0] + '/disable', headers=H)
    rec('C2 课节全部取消后可停用门店 200',
        r.status_code == 200 and r.json().get('status') == 'DISABLED', r.text)

    r = call(c, 'POST', '/api/schedule/sessions', headers=H,
             json={'template_id': tpl1,
                   'starts_at': (today + timedelta(days=20)).strftime('%Y-%m-%d %H:%M'),
                   'cancel_deadline_hours': 2})
    if r.status_code == 200:
        note('停用门店仍可排课（缺口）',
             'POST /api/schedule/sessions 与 /api/schedule/weekly 均未校验 store.status，'
             '已停用门店仍能建课（services/schedule.py 的 create_session / '
             'generate_weekly_sessions 无门店状态判断）')

    bad = [s for s in STATUSES if s[2] >= 500]
    rec('A8 全流程零 500', not bad, bad)

print('__RESULT__' + json.dumps({'checks': OUT, 'notes': NOTES,
                                 's500': bad}, ensure_ascii=False))
'''


def _run(cmd: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True,
                          text=True, timeout=420)


@pytest.mark.usefixtures('_schema')
def test_a_empty_db_self_service_flow():
    """A：全新空库从 0 开一家店（alembic 建表，无 seed 业务数据，零 500）。"""
    for suffix in ('', '-wal', '-shm'):
        path = Path(FRESH_DB + suffix)
        if path.exists():
            path.unlink()

    env = dict(os.environ)
    env.update({
        'APP_ENV': 'test', 'DATABASE_URL': 'sqlite:///' + FRESH_DB,
        'JWT_SECRET': 'qa-independent-secret-key-123456', 'SEED_DEMO': 'false',
        'WX_MOCK': 'true', 'WX_SUBSCRIBE_ENABLED': 'false',
        'ATTENDANCE_WINDOW_MINUTES': '0', 'LOG_LEVEL': 'WARNING',
        'PYTHONPATH': str(REPO_ROOT),
    })

    up = _run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], env)
    assert up.returncode == 0, 'alembic upgrade head 失败：\n%s\n%s' % (up.stdout, up.stderr)

    flow = _run([sys.executable, '-c', _FLOW_SRC], env)
    marker = [ln for ln in flow.stdout.splitlines() if ln.startswith('__RESULT__')]
    assert marker, '流程脚本未产出结果：\n%s\n%s' % (flow.stdout, flow.stderr)
    result = json.loads(marker[-1][len('__RESULT__'):])

    failed = [c for c in result['checks'] if not c['ok']]
    detail = '\n'.join('- %s :: %s' % (c['name'], c['detail']) for c in failed)
    print('\n[场景A 观察项]')
    for n in result['notes']:
        print('  · %s：%s' % (n['name'], n['detail']))
    assert not failed, '场景 A 未通过：\n' + detail
    assert not result['s500'], '出现 5xx：%s' % result['s500']


# ======================= B. 权限与角色 =======================
class TestPermission:
    """B：教练/店长只读放宽，写口保持原权限；未登录一律 401。"""

    def test_b_coach_can_read_dictionary_endpoints(self, client, tokens):
        h = tokens['coach_a']
        for path in ('/api/schedule/sessions', '/api/schedule/templates',
                     '/api/tenant/stores', '/api/tenant/coaches'):
            resp = client.get(path, headers=h)
            assert resp.status_code == 200, '%s → %s %s' % (path, resp.status_code, resp.text)

    def test_b_coach_cannot_write(self, client, tokens):
        h = tokens['coach_a']
        store = _mk_store(client, tokens['a'])
        tpl = _mk_template(client, tokens['a'], store['id'])
        cases = [
            ('POST', '/api/schedule/weekly', {'template_id': tpl['id'],
                                             'week_start': '2026-12-07',
                                             'times': ['09:00']}),
            ('POST', '/api/schedule/sessions', {'template_id': tpl['id'],
                                               'starts_at': '2026-12-07 09:00'}),
            ('PATCH', '/api/schedule/templates/' + tpl['id'], {'capacity': 10}),
            ('PUT', '/api/tenant/stores/' + store['id'], {'name': 'x'}),
            ('POST', '/api/tenant/stores/' + store['id'] + '/disable', None),
            ('PATCH', '/api/tenant/users/user-coach-a', {'real_name': 'x'}),
            ('POST', '/api/tenant/users/user-coach-a/disable', None),
        ]
        for method, path, body in cases:
            resp = client.request(method, path, headers=h,
                                  **({'json': body} if body else {}))
            assert resp.status_code == 403, '%s %s → %s' % (method, path, resp.status_code)
        assert client.get('/api/tenant/users', headers=h).status_code == 403

    def test_b_store_manager_reads_ok_writes_denied(self, client, tokens):
        store = _mk_store(client, tokens['a'])
        mgr = _mk_user(client, tokens['a'], 'STORE_MANAGER', store['id'])
        h = _login(client, _username_of(client, tokens['a'], mgr['id']))
        for path in ('/api/schedule/sessions', '/api/schedule/templates',
                     '/api/tenant/stores', '/api/tenant/coaches'):
            resp = client.get(path, headers=h)
            assert resp.status_code == 200, '%s → %s' % (path, resp.status_code)
        assert client.get('/api/tenant/users', headers=h).status_code == 403
        assert client.put('/api/tenant/stores/' + store['id'], headers=h,
                          json={'name': 'x'}).status_code == 403
        assert client.post('/api/tenant/stores/' + store['id'] + '/disable',
                           headers=h).status_code == 403

    def test_b_anonymous_all_401(self, client, tokens):
        store = _mk_store(client, tokens['a'])
        tpl = _mk_template(client, tokens['a'], store['id'])
        cases = [
            ('GET', '/api/auth/me', None),
            ('GET', '/api/schedule/sessions', None),
            ('GET', '/api/schedule/templates', None),
            ('GET', '/api/tenant/stores', None),
            ('GET', '/api/tenant/coaches', None),
            ('GET', '/api/tenant/users', None),
            ('POST', '/api/schedule/weekly', {'template_id': tpl['id'],
                                             'week_start': '2026-12-07',
                                             'times': ['09:00']}),
            ('PUT', '/api/tenant/stores/' + store['id'], {'name': 'x'}),
            ('PATCH', '/api/schedule/templates/' + tpl['id'], {'capacity': 9}),
        ]
        for method, path, body in cases:
            resp = client.request(method, path, **({'json': body} if body else {}))
            assert resp.status_code == 401, '%s %s → %s' % (method, path, resp.status_code)


def _username_of(client, admin_h: dict, user_id: str) -> str:
    """从账号列表里反查用户名（新建接口只回 id/username/role，这里统一走列表）。"""
    for u in client.get('/api/tenant/users', headers=admin_h).json():
        if u['id'] == user_id:
            return u['username']
    raise AssertionError('账号不存在：%s' % user_id)


class TestFrontendGuard:
    """B（静态）：侧栏与路由守卫按角色收敛，且守卫在发请求之前。"""

    def test_b_page_roles_matrix(self):
        src = WEB_JS.read_text(encoding='utf-8')
        pages = _js_block(src, 'PAGES')
        roles = _js_block(src, 'PAGE_ROLES')
        assert len(pages) == 9, 'PAGES 应有 9 项，实际 %s' % list(pages)
        assert {k for k, v in roles.items() if 'TENANT_ADMIN' in v} == set(pages), \
            '馆主应能看到全部 9 项菜单'
        assert {k for k, v in roles.items() if 'STORE_MANAGER' in v} == {
            'dashboard', 'members', 'payments', 'schedule', 'reconcile'}
        for hidden in ('stores', 'users', 'products'):
            assert 'STORE_MANAGER' not in roles[hidden], '店长不应看到 %s' % hidden
        assert [k for k, v in roles.items() if 'COACH' in v] == ['schedule'], \
            '教练只应看到 schedule'

    def test_b_guard_runs_before_request(self):
        src = WEB_JS.read_text(encoding='utf-8')
        guard = src.index('if (!canView(page))')
        render = src.index('if (render) await render(content)')
        assert guard < render, '守卫必须在渲染（发请求）之前'
        assert '无权访问' in src[guard:guard + 400], '无权限页应渲染「无权访问」空态'
        # 教练视角无写按钮
        assert "currentUser.role !== 'COACH'" in src
        assert re.search(r'\$\{canEdit \?[\s\S]{0,200}?sc-weekly', src), \
            '「按周生成」等写按钮必须在 canEdit 分支内'

    def test_b_sidebar_matches_pages(self):
        html = WEB_HTML.read_text(encoding='utf-8')
        nav = set(re.findall(r'data-page="([a-z]+)"', html))
        pages = set(_js_block(WEB_JS.read_text(encoding='utf-8'), 'PAGES'))
        assert nav == pages, '侧栏 %s 与 PAGES %s 不一致' % (sorted(nav), sorted(pages))


def _js_block(src: str, name: str) -> dict:
    """粗解析 app.js 里的扁平对象字面量（键 → 字符串或字符串数组）。"""
    start = src.index('const %s = {' % name)
    body = src[start:src.index('};', start)]
    out = {}
    for key, val in re.findall(r'(\w+):\s*(\[[^\]]*\]|\'[^\']*\')', body):
        out[key] = re.findall(r"'([^']*)'", val) if val.startswith('[') else \
            val.strip("'")
    return out


# ======================= C. 停用规则 =======================
class TestDisableRules:
    """C：门店/账号停用规则与停用后令牌失效。"""

    def test_c_disable_store_requires_no_future_session(self, client, tokens):
        h = tokens['a']
        store = _mk_store(client, h)
        tpl = _mk_template(client, h, store['id'])
        _mk_session(client, h, tpl['id'], _future(days=3))
        resp = client.post('/api/tenant/stores/%s/disable' % store['id'], headers=h)
        assert resp.status_code == 422, resp.text
        message = resp.json()['error']['message']
        assert '1' in message and '节' in message, '文案应含课节数：%s' % message

        session_id = [s for s in _sessions(client, h, store_id=store['id'])][0]['id']
        assert client.post('/api/schedule/sessions/%s/cancel' % session_id,
                           headers=h).status_code == 200
        resp = client.post('/api/tenant/stores/%s/disable' % store['id'], headers=h)
        assert resp.status_code == 200 and resp.json()['status'] == 'DISABLED'
        listed = {s['id']: s['status'] for s in
                  client.get('/api/tenant/stores', headers=h).json()}
        assert listed[store['id']] == 'DISABLED'

    def test_c_disable_self_rejected(self, client, tokens):
        resp = client.post('/api/tenant/users/user-admin-a/disable', headers=tokens['a'])
        assert resp.status_code == 422, resp.text
        assert '自己' in resp.json()['error']['message']

    def test_c_last_admin_cannot_be_demoted(self, client, tokens):
        resp = client.patch('/api/tenant/users/user-admin-a', headers=tokens['a'],
                            json={'role': 'COACH'})
        assert resp.status_code == 422, resp.text
        assert '馆主' in resp.json()['error']['message']

    def test_c_last_admin_cannot_be_disabled(self, client, tokens):
        """自停用规则会先拦人，因此用平台管理员身份验证同一守卫分支。"""
        platform = _ensure_platform_admin()
        h = _login(client, platform['username'])
        resp = client.post('/api/tenant/users/user-admin-a/disable', headers=h)
        assert resp.status_code == 422, resp.text
        assert '馆主' in resp.json()['error']['message']

    def test_c_disable_coach_kills_login_and_old_token(self, client, tokens):
        h = tokens['a']
        store = _mk_store(client, h)
        user = _mk_user(client, h, 'COACH', store['id'])
        username = _username_of(client, h, user['id'])
        coach_h = _login(client, username)

        assert client.get('/api/schedule/sessions',
                          headers=coach_h).status_code == 200
        assert client.post('/api/tenant/users/%s/disable' % user['id'],
                           headers=h).status_code == 200
        assert client.post('/api/auth/login',
                           json={'username': username,
                                 'password': QA_PASSWORD}).status_code == 401
        # 关键：已签发的旧 token 在停用后必须立即失效
        assert client.get('/api/schedule/sessions',
                          headers=coach_h).status_code == 401
        assert client.get('/api/coach/today', headers=coach_h).status_code == 401
        assert client.get('/api/auth/me', headers=coach_h).status_code == 401


def _ensure_platform_admin() -> dict:
    """测试夹具：造一个平台管理员（校验「最后一位馆主」守卫的唯一途径）。"""
    username = 'qa-platform'
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            user = User(id=_uid('user-platform'), tenant_id='t-a', username=username,
                        password_hash=hash_password(QA_PASSWORD),
                        real_name='QA平台管理员', role='PLATFORM_ADMIN')
            db.add(user)
            db.commit()
        return {'username': username, 'id': user.id}


class TestGapClosure:
    """G1/G4/G5 缺口修复后的独立复验（不复用工程师用例的断言与数据）。"""

    def test_c1_disabled_store_rejects_new_sessions(self, client, tokens):
        """G1：门店停用后，单节课与按周生成都必须被拒且零落库。"""
        h = tokens['a']
        store = _mk_store(client, h)
        tpl = _mk_template(client, h, store['id'])
        name = _template_name(client, h, tpl['id'])
        _mk_session(client, h, tpl['id'], _future(days=2))
        for row in _sessions(client, h):
            if row['course_name'] == name:
                client.post('/api/schedule/sessions/%s/cancel' % row['id'], headers=h)
        assert client.post('/api/tenant/stores/%s/disable' % store['id'],
                           headers=h).status_code == 200

        before = _count_by_tpl(client, h, name)
        resp = client.post('/api/schedule/sessions', headers=h,
                           json={'template_id': tpl['id'], 'starts_at': _future(days=4)})
        assert resp.status_code == 422, resp.text
        assert '停用' in resp.json()['error']['message'], resp.text

        resp = client.post('/api/schedule/weekly', headers=h,
                           json={'template_id': tpl['id'], 'week_start': '2026-12-07',
                                 'times': ['09:00']})
        assert resp.status_code == 422, resp.text
        assert _count_by_tpl(client, h, name) == before, '停用门店不得新增课节'

    def test_c1_storeless_template_still_allowed(self, client, tokens):
        """G1 边界：不属于任何门店的模板（store_id='')不应被门店规则误伤。"""
        h = tokens['a']
        tpl = _mk_template(client, h, '')
        resp = client.post('/api/schedule/sessions', headers=h,
                           json={'template_id': tpl['id'], 'starts_at': _future(days=5)})
        assert resp.status_code == 200, resp.text

    def test_c2_disabled_coach_dropped_from_dropdown(self, client, tokens):
        """G4：停用教练默认不出现在教练字典，按 include_disabled 仍可取到（名称映射用）。"""
        h = tokens['a']
        store = _mk_store(client, h)
        user = _mk_user(client, h, 'COACH', store['id'])
        assert client.post('/api/tenant/users/%s/disable' % user['id'],
                           headers=h).status_code == 200

        default = [c for c in client.get('/api/tenant/coaches', headers=h).json()
                   if c['id'] == user['id']]
        assert default == [], '停用教练不应出现在默认教练字典'
        with_disabled = [c for c in client.get(
            '/api/tenant/coaches?include_disabled=1', headers=h).json()
            if c['id'] == user['id']]
        assert len(with_disabled) == 1 and with_disabled[0]['status'] == 'DISABLED'

    def test_d3_skipped_counts_parsed_times(self, client, tokens):
        """G5：等价写法（9:00 与 09:00）不得让 skipped 虚高。"""
        h = tokens['a']
        store = _mk_store(client, h)
        tpl = _mk_template(client, h, store['id'])
        body = {'template_id': tpl['id'], 'week_start': '2026-12-21',
                'times': ['9:00', '09:00']}
        first = client.post('/api/schedule/weekly', headers=h, json=body).json()
        assert first['created'] == 7 and first['skipped'] == 0, first
        again = client.post('/api/schedule/weekly', headers=h, json=body).json()
        assert again['created'] == 0 and again['skipped'] == 7, again

    def test_f1_weekly_form_receives_stores(self):
        """前端：按周生成弹窗自行计算门店标签，不得引用 renderSchedule 作用域的变量。"""
        src = WEB_JS.read_text(encoding='utf-8')
        assert re.search(r'openWeeklyForm\(templates,\s*stores', src), \
            'openWeeklyForm 必须显式接收 stores'
        body = src[src.index('function openWeeklyForm('):]
        body = body[:body.index('\n}\n')]
        assert 'const tplLabel' in body, '弹窗内部需自算模板标签（避免跨作用域引用）'


def _future(days: int = 3, hour: int = 10) -> str:
    from datetime import datetime, timedelta
    return (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d') + \
        ' %02d:00' % hour


def _next_monday(weeks_ahead: int = 1) -> str:
    """下一个周一（周一=0），用于 days 场景的 week_start。"""
    from datetime import date, timedelta
    today = date.today()
    delta = (7 - today.weekday()) % 7 or 7
    return (today + timedelta(days=delta + 7 * weeks_ahead)).isoformat()


def _weekday_of(starts_at: str) -> int:
    from datetime import datetime
    return datetime.strptime(starts_at, '%Y-%m-%d %H:%M').weekday()


# ======================= G2. 按周生成可选「周几」 =======================
class TestWeeklyDays:
    """G2：`POST /api/schedule/weekly` 的 days 参数（只验这一项 + 其边界）。"""

    def _tpl(self, client, h: dict) -> dict:
        store = _mk_store(client, h)
        return _mk_template(client, h, store['id'])

    def test_g2_selected_weekdays_only(self, client, tokens):
        """days=[1,3,5] × 2 时段 → 6 节，且只落在周一/三/五。"""
        h = tokens['a']
        tpl = self._tpl(client, h)
        week = _next_monday(2)
        resp = client.post('/api/schedule/weekly', headers=h, json={
            'template_id': tpl['id'], 'week_start': week,
            'times': ['09:00', '19:00'], 'days': [1, 3, 5]})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body['created'] == 6, body
        assert body['skipped'] == 0, body
        assert body['days'] == [1, 3, 5], body

        rows = [s for s in _sessions(client, h) if s['course_name'] == tpl['name']]
        assert len(rows) == 6, len(rows)
        assert {_weekday_of(s['starts_at']) for s in rows} == {0, 2, 4}, \
            sorted({s['starts_at'] for s in rows})
        assert {s['starts_at'][11:] for s in rows} == {'09:00', '19:00'}
        week_days = {s['starts_at'][:10] for s in rows}
        assert len(week_days) == 3 and all(d >= week for d in week_days), week_days

    def test_g2_repeat_is_idempotent(self, client, tokens):
        h = tokens['a']
        tpl = self._tpl(client, h)
        body = {'template_id': tpl['id'], 'week_start': _next_monday(3),
                'times': ['09:00', '19:00'], 'days': [1, 3, 5]}
        assert client.post('/api/schedule/weekly', headers=h,
                           json=body).json()['created'] == 6
        again = client.post('/api/schedule/weekly', headers=h, json=body).json()
        assert again['created'] == 0 and again['skipped'] == 6, again
        assert _count_by_tpl(client, h, tpl['name']) == 6, '重复提交不得新增'

    def test_g2_days_absent_keeps_full_week(self, client, tokens):
        """向后兼容：不传 days 仍生成整周 7 天。"""
        h = tokens['a']
        tpl = self._tpl(client, h)
        resp = client.post('/api/schedule/weekly', headers=h, json={
            'template_id': tpl['id'], 'week_start': _next_monday(4),
            'times': ['09:00', '19:00']})
        body = resp.json()
        assert resp.status_code == 200, resp.text
        assert body['created'] == 14 and body['days'] == [1, 2, 3, 4, 5, 6, 7], body
        rows = [s for s in _sessions(client, h) if s['course_name'] == tpl['name']]
        assert {_weekday_of(s['starts_at']) for s in rows} == set(range(7))

    @pytest.mark.parametrize('days', [[], [0], [8], [0, 8], [True], [-1], [1.5]])
    def test_g2_invalid_days_rejected_without_sessions(self, client, tokens, days):
        h = tokens['a']
        tpl = self._tpl(client, h)
        resp = client.post('/api/schedule/weekly', headers=h, json={
            'template_id': tpl['id'], 'week_start': _next_monday(5),
            'times': ['09:00'], 'days': days})
        assert 400 <= resp.status_code < 500, '%s → %s %s' % (
            days, resp.status_code, resp.text)
        assert _count_by_tpl(client, h, tpl['name']) == 0, '非法 days 不得落库'

    @pytest.mark.parametrize('days', [['1'], ['3'], ['07'], ['mon'], ['1.5'], ['true'],
                                      ['1', '3']])
    def test_g2_string_days_rejected(self, client, tokens, days):
        """字符串星期一律 4xx：pydantic 宽松模式会把 '1' 静默转成周一（缺陷已修，留哨兵）。"""
        h = tokens['a']
        tpl = self._tpl(client, h)
        resp = client.post('/api/schedule/weekly', headers=h, json={
            'template_id': tpl['id'], 'week_start': _next_monday(6),
            'times': ['09:00'], 'days': days})
        assert 400 <= resp.status_code < 500, '%s → %s %s' % (days, resp.status_code, resp.text)
        assert _count_by_tpl(client, h, tpl['name']) == 0, '非法 days 不得落库'

    def test_g6_snap_exact_dates_202603(self, client, tokens):
        """钉死日期矩阵：周二 2026-03-03 → 落 03-02/04/06，绝不出现 03-03/05/07。"""
        h = tokens['a']
        tpl = self._tpl(client, h)
        body = {'template_id': tpl['id'], 'week_start': '2026-03-03',
                'times': ['09:00'], 'days': [1, 3, 5]}
        resp = client.post('/api/schedule/weekly', headers=h, json=body)
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result['created'] == 3, result
        assert result['week_start'] == '2026-03-02', result
        assert result['requested_week_start'] == '2026-03-03', result

        rows = [s for s in _sessions(client, h) if s['course_name'] == tpl['name']]
        dates = sorted(s['starts_at'] for s in rows)
        assert dates == ['2026-03-02 09:00', '2026-03-04 09:00', '2026-03-06 09:00'], dates
        assert not ({'2026-03-03', '2026-03-05', '2026-03-07'}
                    & {s['starts_at'][:10] for s in rows}), dates

        # 幂等：换用周一入参提交同周同参数 → 不得重复建课
        again = client.post('/api/schedule/weekly', headers=h,
                            json=dict(body, week_start='2026-03-02')).json()
        assert again['created'] == 0 and again['week_start'] == '2026-03-02', again
        assert _count_by_tpl(client, h, tpl['name']) == 3

    def test_g6_sunday_week_start_snaps_to_previous_monday(self, client, tokens):
        """周日 2026-03-08 + days=[1] → 落该周周一 2026-03-02（不是次日周一）。"""
        h = tokens['a']
        tpl = self._tpl(client, h)
        resp = client.post('/api/schedule/weekly', headers=h, json={
            'template_id': tpl['id'], 'week_start': '2026-03-08',
            'times': ['09:00'], 'days': [1]})
        result = resp.json()
        assert resp.status_code == 200, resp.text
        assert result['week_start'] == '2026-03-02', result
        rows = [s for s in _sessions(client, h) if s['course_name'] == tpl['name']]
        assert [s['starts_at'] for s in rows] == ['2026-03-02 09:00'], rows

    def test_g2_duplicate_days_deduped(self, client, tokens):
        """days=[1,1,3] → 去重成 2 天，不产生重复课节。"""
        h = tokens['a']
        tpl = self._tpl(client, h)
        resp = client.post('/api/schedule/weekly', headers=h, json={
            'template_id': tpl['id'], 'week_start': _next_monday(7),
            'times': ['09:00', '19:00'], 'days': [1, 1, 3]})
        body = resp.json()
        assert resp.status_code == 200, resp.text
        assert body['days'] == [1, 3], body
        assert body['created'] == 4 and body['skipped'] == 0, body
        rows = [s for s in _sessions(client, h) if s['course_name'] == tpl['name']]
        assert len(rows) == 4 and len({s['starts_at'] for s in rows}) == 4

    def test_g2_disabled_store_with_days_rejected(self, client, tokens):
        """与门店停用联动：停用门店 + days=[1] → 422 且零落库。"""
        h = tokens['a']
        store = _mk_store(client, h)
        tpl = _mk_template(client, h, store['id'])
        assert client.post('/api/tenant/stores/%s/disable' % store['id'],
                           headers=h).status_code == 200
        resp = client.post('/api/schedule/weekly', headers=h, json={
            'template_id': tpl['id'], 'week_start': _next_monday(8),
            'times': ['09:00'], 'days': [1]})
        assert resp.status_code == 422, resp.text
        assert '停用' in resp.json()['error']['message']
        assert _count_by_tpl(client, h, tpl['name']) == 0

    def test_g6_non_monday_week_start_snaps_to_monday(self, client, tokens):
        """week_start 传周三时自动吸附到该周周一，勾周一/三/五仍落周一/三/五。"""
        from datetime import datetime, timedelta
        h = tokens['a']
        tpl = self._tpl(client, h)
        monday = _next_monday(10)
        wednesday = (datetime.strptime(monday, '%Y-%m-%d') + timedelta(days=2)).strftime('%Y-%m-%d')
        resp = client.post('/api/schedule/weekly', headers=h, json={
            'template_id': tpl['id'], 'week_start': wednesday,
            'times': ['09:00'], 'days': [1, 3, 5]})
        body = resp.json()
        assert resp.status_code == 200, resp.text
        assert body['week_start'] == monday, body
        assert body['requested_week_start'] == wednesday, body
        assert body['created'] == 3, body
        rows = [s for s in _sessions(client, h) if s['course_name'] == tpl['name']]
        assert {_weekday_of(s['starts_at']) for s in rows} == {0, 2, 4}, \
            sorted(s['starts_at'] for s in rows)
        expect_days = {(datetime.strptime(monday, '%Y-%m-%d')
                        + timedelta(days=offset)).strftime('%Y-%m-%d') for offset in (0, 2, 4)}
        assert {s['starts_at'][:10] for s in rows} == expect_days

    def test_g6_idempotent_across_week_start_forms(self, client, tokens):
        """同一周换一种 week_start 写法（周三 / 周一）不得重复建课。"""
        from datetime import datetime, timedelta
        h = tokens['a']
        tpl = self._tpl(client, h)
        monday = _next_monday(11)
        wednesday = (datetime.strptime(monday, '%Y-%m-%d') + timedelta(days=2)).strftime('%Y-%m-%d')
        base = {'template_id': tpl['id'], 'times': ['09:00'], 'days': [1]}
        first = client.post('/api/schedule/weekly', headers=h,
                            json=dict(base, week_start=wednesday)).json()
        assert first['created'] == 1, first
        again = client.post('/api/schedule/weekly', headers=h,
                            json=dict(base, week_start=monday)).json()
        assert again['created'] == 0 and again['week_start'] == monday, again
        assert _count_by_tpl(client, h, tpl['name']) == 1

    @pytest.mark.parametrize('bad_week', ['', '2026/01/05', 'abc', '2026-13-01'])
    def test_g6_invalid_week_start_rejected(self, client, tokens, bad_week):
        h = tokens['a']
        tpl = self._tpl(client, h)
        resp = client.post('/api/schedule/weekly', headers=h, json={
            'template_id': tpl['id'], 'week_start': bad_week,
            'times': ['09:00'], 'days': [1]})
        assert 400 <= resp.status_code < 500, '%r → %s' % (bad_week, resp.status_code)
        assert _count_by_tpl(client, h, tpl['name']) == 0

    def test_g2_permissions_coach_403_manager_200(self, client, tokens):
        store = _mk_store(client, tokens['a'])
        tpl = _mk_template(client, tokens['a'], store['id'])
        payload = {'template_id': tpl['id'], 'week_start': _next_monday(9),
                   'times': ['09:00'], 'days': [1]}
        assert client.post('/api/schedule/weekly', headers=tokens['coach_a'],
                           json=payload).status_code == 403
        mgr = _mk_user(client, tokens['a'], 'STORE_MANAGER', store['id'])
        mgr_h = _login(client, _username_of(client, tokens['a'], mgr['id']))
        resp = client.post('/api/schedule/weekly', headers=mgr_h, json=payload)
        assert resp.status_code == 200 and resp.json()['created'] == 1, resp.text

    def test_g2_frontend_weekday_checkboxes(self):
        """前端：7 个星期 checkbox、默认全选、未选时提前 return 不发请求、成功 toast 含节数。"""
        src = WEB_JS.read_text(encoding='utf-8')
        block = src[src.index('const WEEKDAY_OPTIONS = ['):]
        block = block[:block.index('];')]
        assert len(re.findall(r'value: \d', block)) == 7, '应有 7 个星期选项'
        assert {int(v) for v in re.findall(r'value: (\d)', block)} == set(range(1, 8))

        form = src[src.index('function openWeeklyForm('):]
        form = form[:form.index('\n}\n')]
        assert re.search(r'value="\$\{d\.value\}"\s+checked', form), '星期应默认全选'
        guard = form.index("if (!days.length)")
        post = form.index("API.request('/api/schedule/weekly'")
        assert guard < post, '未勾选任何一天时必须提前 return，不得发请求'
        assert '请至少选择一天' in form[guard:guard + 80]
        assert "'已生成 ' + r.created + ' 节，跳过 ' + r.skipped + ' 节'" in form, \
            '成功提示应含生成/跳过节数'
        assert 'days,' in form or 'days: days' in form, '请求体必须带上 days'
        # G6：week_start 被吸附到周一时，必须告诉馆主实际生效的是哪一周
        assert 'r.requested_week_start !== r.week_start' in form, '吸附后应有独立提示分支'
        assert '日（周一）所在周生成' in form, '吸附提示应写明生效周一'


# ======================= D. 入参与幂等 =======================
class TestValidationAndIdempotency:
    """D：时段/截止小时/容量/时长/卡种边界 + 时段去重 + 跨租户。"""

    @pytest.mark.parametrize('bad_time', ['25:00', '09:99', '-1:00', '09:60'])
    def test_d_invalid_times_rejected_without_sessions(self, client, tokens, bad_time):
        h = tokens['a']
        store = _mk_store(client, h)
        tpl = _mk_template(client, h, store['id'])
        before = _count_by_tpl(client, h, _template_name(client, h, tpl['id']))
        resp = client.post('/api/schedule/weekly', headers=h, json={
            'template_id': tpl['id'], 'week_start': '2026-12-07', 'times': [bad_time]})
        assert 400 <= resp.status_code < 500, '%s → %s' % (bad_time, resp.status_code)
        after = _count_by_tpl(client, h, _template_name(client, h, tpl['id']))
        assert after == before, '非法时段不得落库：%s → %s' % (before, after)

    @pytest.mark.parametrize('hours', [-3, 100])
    def test_d_invalid_deadline_hours_rejected(self, client, tokens, hours):
        h = tokens['a']
        store = _mk_store(client, h)
        tpl = _mk_template(client, h, store['id'])
        name = _template_name(client, h, tpl['id'])
        before = _count_by_tpl(client, h, name)
        resp = client.post('/api/schedule/weekly', headers=h, json={
            'template_id': tpl['id'], 'week_start': '2026-12-14',
            'times': ['09:00'], 'cancel_deadline_hours': hours})
        assert 400 <= resp.status_code < 500, '%s → %s' % (hours, resp.status_code)
        assert _count_by_tpl(client, h, name) == before
        assert client.post('/api/schedule/sessions', headers=h, json={
            'template_id': tpl['id'], 'starts_at': '2026-12-14 09:00',
            'cancel_deadline_hours': hours}).status_code == 400

    def test_d_duplicate_times_in_one_request_not_duplicated(self, client, tokens):
        h = tokens['a']
        store = _mk_store(client, h)
        tpl = _mk_template(client, h, store['id'])
        name = _template_name(client, h, tpl['id'])
        resp = client.post('/api/schedule/weekly', headers=h, json={
            'template_id': tpl['id'], 'week_start': '2026-12-07',
            'times': ['09:00', '09:00']})
        assert resp.status_code == 200, resp.text
        assert resp.json()['created'] == 7, '同一请求内重复时段必须去重：%s' % resp.json()
        rows = [s for s in _sessions(client, h) if s['course_name'] == name]
        assert len(rows) == 7 and len({s['starts_at'] for s in rows}) == 7

    @pytest.mark.parametrize('payload', [
        {'capacity': 201}, {'duration_minutes': 10},
        {'duration_minutes': 301}, {'allowed_ptypes': 'VIP'}])
    def test_d_template_field_bounds_rejected(self, client, tokens, payload):
        h = tokens['a']
        store = _mk_store(client, h)
        body = {'name': 'QA边界' + uuid.uuid4().hex[:4], 'store_id': store['id'],
                'capacity': 8, 'duration_minutes': 60}
        body.update(payload)
        resp = client.post('/api/schedule/templates', headers=h, json=body)
        assert 400 <= resp.status_code < 500, '%s → %s %s' % (
            payload, resp.status_code, resp.text)

    @pytest.mark.parametrize('payload', [{'capacity': 0}, {'duration_minutes': 0}])
    def test_d_template_zero_values_rejected(self, client, tokens, payload):
        """0 是显式非法输入，必须 4xx；曾被 ``or 15`` 静默兜底成 15（已修，留作哨兵）。"""
        h = tokens['a']
        store = _mk_store(client, h)
        body = {'name': 'QA零值' + uuid.uuid4().hex[:4], 'store_id': store['id'],
                'capacity': 8, 'duration_minutes': 60}
        body.update(payload)
        resp = client.post('/api/schedule/templates', headers=h, json=body)
        assert 400 <= resp.status_code < 500, resp.text

    def test_d_create_user_cannot_grant_platform_admin(self, client, tokens):
        """馆主不得在租户内创建 PLATFORM_ADMIN（否则是跨租户提权入口）。

        该缺陷曾于验收首轮复现（返回 200 且账号可登录），工程师随后修正为
        ``_check_role``；此用例保留为回归哨兵。
        """
        resp = client.post('/api/tenant/users', headers=tokens['a'], json={
            'username': _uid('esc'), 'password': QA_PASSWORD,
            'real_name': '越权平台账号', 'role': 'PLATFORM_ADMIN'})
        assert resp.status_code in (400, 403), resp.text

    def test_d_cross_tenant_writes_blocked(self, client, tokens):
        a, b = tokens['a'], tokens['b']
        store_b = _mk_store(client, b)
        tpl_b = _mk_template(client, b, store_b['id'])
        user_b = _mk_user(client, b, 'COACH', store_b['id'])

        cases = [
            ('PUT', '/api/tenant/stores/' + store_b['id'], {'name': 'hijack'}),
            ('POST', '/api/tenant/stores/' + store_b['id'] + '/disable', None),
            ('PATCH', '/api/tenant/users/' + user_b['id'], {'real_name': 'hijack'}),
            ('POST', '/api/tenant/users/' + user_b['id'] + '/disable', None),
            ('PATCH', '/api/schedule/templates/' + tpl_b['id'], {'capacity': 3}),
            ('POST', '/api/schedule/weekly', {'template_id': tpl_b['id'],
                                             'week_start': '2026-12-07',
                                             'times': ['09:00']}),
            ('POST', '/api/schedule/sessions', {'template_id': tpl_b['id'],
                                               'starts_at': '2026-12-07 09:00'}),
        ]
        for method, path, body in cases:
            resp = client.request(method, path, headers=a,
                                  **({'json': body} if body else {}))
            assert resp.status_code in (403, 404), '%s %s → %s' % (
                method, path, resp.status_code)

        # 交叉读取不得泄露：A 看不到 B 的模板
        leaked = [t for t in client.get('/api/schedule/templates?store_id=' + store_b['id'],
                                        headers=a).json()]
        assert leaked == [], '跨租户模板泄露：%s' % leaked


def _template_name(client, h: dict, template_id: str) -> str:
    for t in client.get('/api/schedule/templates', headers=h).json():
        if t['id'] == template_id:
            return t['name']
    raise AssertionError('模板不存在：%s' % template_id)


# ======================= E. 回归与静态质量 =======================
class TestStaticQuality:
    """E：静态资源、前端语法、设计系统（零硬编码色值）。"""

    def test_e_admin_assets_served(self, client):
        for path in ('/admin/', '/admin/assets/app.js', '/admin/assets/app.css'):
            assert client.get(path).status_code == 200, path

    def test_e_app_js_syntax_ok(self):
        if not _has_node():
            pytest.skip('未安装 node，改用括号配平兜底检查')
        proc = subprocess.run(['node', '--check', str(WEB_JS)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr

    def test_e_toast_not_stretched_by_tokens_top(self):
        """tokens.css 的 .toast 带 top:24px；app.css 覆盖为贴底时必须显式 top:auto。

        否则 position:fixed 同时有 top 与 bottom 会把元素上下撑满，
        toast 变成覆盖大半屏的深色长条（真实浏览器实测 448px 高）。
        """
        tokens = (REPO_ROOT / 'web' / 'assets' / 'tokens.css').read_text(encoding='utf-8')
        assert re.search(r'\.toast\{[^}]*top:24px', tokens), '前置条件变化：tokens.css 不再设 top'
        block = re.search(r'^\.toast \{[^}]*\}',
                          WEB_CSS.read_text(encoding='utf-8'), re.M).group(0)
        assert 'top: auto' in block, 'app.css 的 .toast 缺 top:auto，会被 top+bottom 撑满'
        assert 'bottom: 28px' in block, 'toast 仍应贴底显示'

    def test_e_no_new_hardcoded_colors(self):
        """设计系统 FROZEN：新增前端代码不得出现 #RRGGBB / rgb() 字面量。"""
        proc = subprocess.run(['git', 'diff', '-U0', '--', 'web/'],
                              cwd=str(REPO_ROOT), capture_output=True, text=True)
        if proc.returncode != 0:
            pytest.skip('git 不可用：%s' % proc.stderr.strip())
        pattern = re.compile(r'#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(')
        offenders = [ln[1:].strip() for ln in proc.stdout.splitlines()
                     if ln.startswith('+') and not ln.startswith('+++')
                     and pattern.search(ln)]
        assert not offenders, '新增硬编码色值：\n' + '\n'.join(offenders)


def _has_node() -> bool:
    try:
        return subprocess.run(['node', '-v'], capture_output=True).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
