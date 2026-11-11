/* 管理后台 SPA：API 客户端 + hash 路由 + 各模块渲染。
 * 安全约定：access_token 仅存 sessionStorage（关闭标签即清），刷新令牌不持久化。
 */
'use strict';

/* ================= API 客户端 ================= */
const API = {
  base: '',
  get token() { return sessionStorage.getItem('access_token') || ''; },
  set token(v) { v ? sessionStorage.setItem('access_token', v) : sessionStorage.removeItem('access_token'); },

  async request(path, { method = 'GET', body } = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (this.token) headers['Authorization'] = 'Bearer ' + this.token;
    let lastErr;
    for (let attempt = 0; attempt < 3; attempt++) {
      let res;
      try {
        res = await fetch(this.base + path, {
          method, headers,
          body: body === undefined ? undefined : JSON.stringify(body),
        });
      } catch (e) {
        lastErr = new Error('网络连接失败，请检查服务是否启动');
        continue; // fetch 失败视为离线，重试
      }
      let data = {};
      try { data = await res.json(); } catch (e) { /* noop */ }
      if (res.status === 401 && this.token) {
        this.token = '';
        showLogin();
        throw new Error('登录已过期，请重新登录');
      }
      if (res.ok) return data;
      const msg = (data.error && data.error.message) || ('请求失败（' + res.status + '）');
      if (res.status >= 500) { lastErr = new Error(msg); continue; } // 5xx 重试
      throw new Error(msg); // 4xx 不重试
    }
    throw lastErr || new Error('请求失败');
  },
};

/* ================= 通用工具 ================= */
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtCents = (c) => '¥' + (Number(c || 0) / 100).toFixed(2);
const yuanToCents = (y) => Math.round(Number(y || 0) * 100);

function showToast(msg, isErr) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.className = 'toast'; }, 2600);
}

function statusTag(kind, v) {
  const map = {
    enroll: { BOOKED: ['green', '已预约'], WAITLIST: ['mist', '候补'], CANCELLED: ['gray', '已取消'], ATTENDED: ['green', '已到课'], NO_SHOW: ['clay', '爽约'] },
    session: { OPEN: ['green', '可约'], FULL: ['clay', '已满'], CLOSED: ['gray', '已截止'], CANCELLED: ['gray', '已取消'], FINISHED: ['mist', '已结束'] },
    method: { CASH: ['gray', '现金'], ALIPAY: ['mist', '支付宝'], WECHAT: ['green', '微信'], CARD: ['mist', '刷卡'], OTHER: ['gray', '其他'] },
    ptype: { PACKAGE: ['green', '课程包'], PRIVATE: ['mist', '私教'], STORED: ['clay', '储值'] },
  };
  const [cls, label] = (map[kind] && map[kind][v]) || ['gray', v];
  return '<span class="tag ' + cls + '">' + label + '</span>';
}

/* ================= 登录 / 布局 ================= */
let currentUser = null;

function showLogin() {
  $('#login-view').style.display = 'flex';
  $('#app-view').style.display = 'none';
  currentUser = null;
}

function showApp(user) {
  currentUser = user;
  $('#login-view').style.display = 'none';
  $('#app-view').style.display = 'flex';
  $('#sidebar-user').textContent = user.real_name || user.username;
  $('#topbar-user').textContent = user.real_name || user.username;
  $('#topbar-role').textContent = { TENANT_ADMIN: '馆主', STORE_MANAGER: '店长', COACH: '教练' }[user.role] || user.role;
}

$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('#login-err').textContent = '';
  const btn = e.target.querySelector('button');
  btn.disabled = true;
  try {
    const data = await API.request('/api/auth/login', {
      method: 'POST',
      body: { username: $('#login-username').value.trim(), password: $('#login-password').value },
    });
    API.token = data.access_token;
    showApp(data.user);
    route();
  } catch (err) {
    $('#login-err').textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

$('#logout-btn').addEventListener('click', () => { API.token = ''; location.hash = ''; showLogin(); });

/* ================= 路由 ================= */
const PAGES = {
  dashboard: '经营看板', members: '会员管理', payments: '收款登记',
  products: '商品管理', schedule: '排课管理', reconcile: '账实核对',
};
const ROUTE_NAMES = { dashboard: '经营看板', members: '会员', payments: '收款登记', products: '商品', schedule: '排课', reconcile: '账实核对' };

async function route() {
  if (!API.token) { showLogin(); return; }
  const page = (location.hash || '#/dashboard').replace('#/', '') || 'dashboard';
  $('#topbar-title').textContent = PAGES[page] || '经营看板';
  document.querySelectorAll('#sidebar-nav a').forEach((a) =>
    a.classList.toggle('on', a.dataset.page === page));
  const content = $('#content');
  content.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const render = { dashboard: renderDashboard, members: renderMembers, payments: renderPayments,
                     products: renderProducts, schedule: renderSchedule, reconcile: renderReconcile }[page];
    if (render) await render(content);
    else content.innerHTML = '<div class="empty">页面不存在</div>';
  } catch (err) {
    content.innerHTML = '<div class="empty">加载失败：' + esc(err.message) + '</div>';
  }
}
window.addEventListener('hashchange', route);

/* ================= 弹窗 ================= */
function openModal(html, width) {
  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.innerHTML = '<div class="modal" style="width:' + (width || 520) + 'px">' + html + '</div>';
  mask.addEventListener('click', (e) => { if (e.target === mask) mask.remove(); });
  document.body.appendChild(mask);
  return mask;
}

/* ================= 经营看板 ================= */
async function renderDashboard(el) {
  const [ov, trend, stores, renewals] = await Promise.all([
    API.request('/api/dashboard/overview'),
    API.request('/api/dashboard/trend'),
    API.request('/api/dashboard/stores'),
    API.request('/api/dashboard/renewals'),
  ]);
  const rate = ov.attendance_rate;
  const maxT = Math.max(1, ...trend.map((t) => t.attended));
  el.innerHTML = `
    <div class="kpi-grid">
      <div class="kpi accent"><div class="k-label">今日到课</div><div class="k-value">${ov.today_attended}<span style="font-size:14px"> 人</span></div><div class="k-sub">消课 ${ov.today_consume} 节</div></div>
      <div class="kpi"><div class="k-label">本月收入</div><div class="k-value" style="font-size:22px">${fmtCents(ov.month_income_cents)}</div><div class="k-sub up">线下收款累计</div></div>
      <div class="kpi"><div class="k-label">活跃会员</div><div class="k-value">${ov.active_members}<span style="font-size:14px"> 人</span></div><div class="k-sub">持有有效权益</div></div>
      <div class="kpi ${ov.pending_diffs > 0 ? 'warn' : ''}"><div class="k-label">到课率 / 账实差异</div><div class="k-value">${rate}%</div><div class="k-sub ${ov.pending_diffs > 0 ? 'down' : ''}">${ov.pending_diffs} 笔差异挂起</div></div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-title">近 7 天到课趋势</div>
        <div class="chart-bars">
          ${trend.map((t) => `<div class="bar-col"><div class="bar-val">${t.attended}</div>
            <div class="bar ${t.attended === maxT && t.attended > 0 ? 'hot' : ''}" style="height:${Math.round(t.attended / maxT * 110)}px"></div>
            <div class="bar-lbl">${t.date}</div></div>`).join('')}
        </div>
      </div>
      <div class="card">
        <div class="card-title">门店对比 <span class="hint">累计收入 / 消课</span></div>
        <div class="table-wrap"><table class="tbl">
          <thead><tr><th>门店</th><th class="num">累计收入</th><th class="num">消课节数</th></tr></thead>
          <tbody>${stores.map((s) => `<tr><td>${esc(s.name)}</td><td class="num">${fmtCents(s.income_cents)}</td><td class="num">${s.consume_sessions}</td></tr>`).join('') || '<tr><td colspan="3" class="empty">暂无门店</td></tr>'}</tbody>
        </table></div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">待续卡会员 <span class="hint">剩余课时 ≤2 或 30 天内到期</span></div>
      ${renewals.length ? `<div class="table-wrap"><table class="tbl">
        <thead><tr><th>会员</th><th>手机号</th><th class="num">剩余课时</th><th>到期日</th><th></th></tr></thead>
        <tbody>${renewals.map((r) => `<tr><td>${esc(r.name)}</td><td>${esc(r.phone_mask)}</td><td class="num">${r.remain_sessions}</td><td>${esc(r.expires_at)}</td><td class="actions"><button class="btn btn-secondary btn-sm" onclick="location.hash='#/payments'">去续费</button></td></tr>`).join('')}</tbody>
      </table></div>` : '<div class="empty">暂无待续卡会员</div>'}
    </div>`;
}

/* ================= 会员管理 ================= */
async function renderMembers(el) {
  el.innerHTML = `
    <div class="toolbar">
      <input id="m-keyword" placeholder="搜索姓名 / 手机号" style="width:220px">
      <button class="btn btn-secondary" id="m-search">搜索</button>
      <button class="btn btn-secondary" id="m-import">批量导入</button>
      <div class="spacer"></div>
      <button class="btn btn-primary" id="m-add">+ 新增会员</button>
    </div>
    <div class="card"><div class="table-wrap"><table class="tbl">
      <thead><tr><th>姓名</th><th>手机号</th><th>建档门店</th><th>状态</th><th>建档日期</th><th></th></tr></thead>
      <tbody id="m-tbody"><tr><td colspan="6" class="empty">加载中…</td></tr></tbody>
    </table></div></div>`;

  const load = async (kw) => {
    const rows = await API.request('/api/members' + (kw ? '?keyword=' + encodeURIComponent(kw) : ''));
    $('#m-tbody').innerHTML = rows.length ? rows.map((m) => `<tr>
      <td><a href="javascript:void 0" class="member-link" data-id="${m.id}">${esc(m.name)}</a></td>
      <td>${esc(m.phone_mask)}</td><td>${m.source_store_id || '—'}</td>
      <td>${statusTag('enroll', m.status === 'ACTIVE' ? 'BOOKED' : 'CANCELLED')}</td>
      <td>${esc(m.created_at)}</td>
      <td class="actions"><button class="btn btn-secondary btn-sm member-link" data-id="${m.id}">详情</button></td></tr>`).join('')
      : '<tr><td colspan="6" class="empty">暂无会员</td></tr>';
    document.querySelectorAll('.member-link').forEach((a) =>
      a.addEventListener('click', () => showMemberDetail(a.dataset.id)));
  };
  await load('');
  $('#m-search').addEventListener('click', () => load($('#m-keyword').value.trim()));
  $('#m-keyword').addEventListener('keydown', (e) => { if (e.key === 'Enter') load(e.target.value.trim()); });
  $('#m-add').addEventListener('click', () => openMemberForm(load));
  $('#m-import').addEventListener('click', () => openMemberImport(load));
}

function openMemberForm(load) {
  const mask = openModal(`
    <h3>新增会员</h3>
    <div class="form-grid">
      <div class="field"><label>姓名</label><input id="f-name" placeholder="必填"></div>
      <div class="field"><label>手机号</label><input id="f-phone" placeholder="必填"></div>
      <div class="field full"><label>备注</label><textarea id="f-note" rows="2"></textarea></div>
    </div>
    <div class="form-actions"><button class="btn btn-secondary" onclick="this.closest('.modal-mask').remove()">取消</button>
    <button class="btn btn-primary" id="f-submit">保存</button></div>`);
  mask.querySelector('#f-submit').addEventListener('click', async () => {
    try {
      await API.request('/api/members', { method: 'POST', body: { name: $('#f-name').value.trim(), phone: $('#f-phone').value.trim(), note: $('#f-note').value.trim() } });
      mask.remove(); showToast('会员已创建'); load('');
    } catch (err) { showToast(err.message, true); }
  });
}

function openMemberImport(load) {
  const mask = openModal(`
    <h3>批量导入会员</h3>
    <div class="field"><label>每行一条：手机号,姓名（CSV 格式）</label>
      <textarea id="f-csv" rows="8" placeholder="13800000001,林晓&#10;13800000002,苏青"></textarea></div>
    <div class="form-actions"><button class="btn btn-secondary" onclick="this.closest('.modal-mask').remove()">取消</button>
    <button class="btn btn-primary" id="f-import">导入</button></div>
    <div id="f-result" style="margin-top:12px;font-size:13px"></div>`);
  mask.querySelector('#f-import').addEventListener('click', async () => {
    const rows = $('#f-csv').value.split('\n').map((l) => l.trim()).filter(Boolean)
      .map((l) => { const [phone, name] = l.split(/[,，\t]/); return { phone: (phone || '').trim(), name: (name || '').trim() }; });
    try {
      const r = await API.request('/api/members/import', { method: 'POST', body: { rows } });
      $('#f-result').innerHTML = `成功 <b>${r.ok_count}</b> 条，失败 <b>${r.failed_count}</b> 条` +
        (r.failed.length ? '<br>' + r.failed.map((f) => `第${f.row}行：${f.reason}`).join('<br>') : '');
      load('');
    } catch (err) { showToast(err.message, true); }
  });
}

async function showMemberDetail(id) {
  const [m, ents, ledger] = await Promise.all([
    API.request('/api/members/' + id),
    API.request('/api/payments/entitlements/' + id),
    API.request('/api/payments/ledger/' + id),
  ]);
  openModal(`
    <h3>${esc(m.name)} <span class="tag gray">${esc(m.phone_mask)}</span></h3>
    <div class="card-title" style="margin-top:4px">当前权益</div>
    ${ents.length ? `<div class="table-wrap"><table class="tbl"><thead><tr><th>商品</th><th class="num">剩余/总课时</th><th class="num">余额</th><th>到期</th><th>状态</th></tr></thead>
    <tbody>${ents.map((e) => `<tr><td>${esc(e.product_name)}</td><td class="num">${e.remain_sessions}/${e.total_sessions}</td><td class="num">${e.balance_cents ? fmtCents(e.balance_cents) : '—'}</td><td>${esc(e.expires_at)}</td><td>${statusTag('enroll', e.status === 'ACTIVE' ? 'BOOKED' : 'CANCELLED')}</td></tr>`).join('')}</tbody></table></div>`
      : '<div class="empty">暂无权益</div>'}
    <div class="card-title">流水明细</div>
    ${ledger.length ? `<div class="table-wrap"><table class="tbl"><thead><tr><th>类型</th><th class="num">课时变动</th><th class="num">余额变动</th><th>说明</th><th>时间</th></tr></thead>
    <tbody>${ledger.map((l) => `<tr><td>${statusTag('ptype', { PURCHASE: 'PACKAGE', CONSUME: 'STORED', REFUND: 'STORED', ADJUST: 'STORED' }[l.kind] || l.kind)}</td><td class="num">${l.delta_sessions > 0 ? '+' : ''}${l.delta_sessions}</td><td class="num">${l.delta_cents ? (l.delta_cents > 0 ? '+' : '') + fmtCents(l.delta_cents) : '—'}</td><td>${esc(l.remark)}</td><td>${esc(l.created_at)}</td></tr>`).join('')}</tbody></table></div>`
      : '<div class="empty">暂无流水</div>'}`,
    640);
}

/* ================= 收款登记 ================= */
async function renderPayments(el) {
  const [stores, members, products, pays] = await Promise.all([
    API.request('/api/tenant/stores'),
    API.request('/api/members'),
    API.request('/api/payments/products'),
    API.request('/api/payments'),
  ]);
  el.innerHTML = `
    <div class="card">
      <div class="card-title">收款登记 <span class="hint">线下收款 · 线上记账（金额以分为单位存储）</span></div>
      <div class="form-grid">
        <div class="field"><label>门店</label><select id="p-store">${stores.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select></div>
        <div class="field"><label>会员</label><select id="p-member">${members.map((m) => `<option value="${m.id}">${esc(m.name)} · ${esc(m.phone_mask)}</option>`).join('')}</select></div>
        <div class="field"><label>商品</label><select id="p-product">${products.map((p) => `<option value="${p.id}" data-cents="${p.price_cents}">${esc(p.name)} · ${fmtCents(p.price_cents)}</option>`).join('')}</select></div>
        <div class="field"><label>收款金额（元）</label><input id="p-amount" type="number" step="0.01" min="0"></div>
        <div class="field"><label>收款方式</label><select id="p-method"><option>CASH</option><option>WECHAT</option><option>ALIPAY</option><option>CARD</option><option>OTHER</option></select></div>
        <div class="field"><label>备注</label><input id="p-remark" placeholder="可选"></div>
      </div>
      <div class="form-actions"><button class="btn btn-primary" id="p-submit">确认收款</button></div>
    </div>
    <div class="card">
      <div class="card-title">最近收款记录</div>
      ${pays.length ? `<div class="table-wrap"><table class="tbl"><thead><tr><th>收据号</th><th>商品</th><th class="num">金额</th><th>方式</th><th>时间</th></tr></thead>
      <tbody>${pays.map((p) => `<tr><td>${esc(p.receipt_no)}</td><td>${esc(p.product_name)}</td><td class="num">${fmtCents(p.amount_cents)}</td><td>${statusTag('method', p.method)}</td><td>${esc(p.paid_at)}</td></tr>`).join('')}</tbody></table></div>`
      : '<div class="empty">暂无收款记录</div>'}
    </div>`;

  const syncAmount = () => {
    const opt = $('#p-product').selectedOptions[0];
    if (opt) $('#p-amount').value = (Number(opt.dataset.cents) / 100).toFixed(2);
  };
  $('#p-product').addEventListener('change', syncAmount);
  syncAmount();

  $('#p-submit').addEventListener('click', async () => {
    try {
      const r = await API.request('/api/payments', { method: 'POST', body: {
        store_id: $('#p-store').value, member_id: $('#p-member').value, product_id: $('#p-product').value,
        amount_cents: yuanToCents($('#p-amount').value), method: $('#p-method').value,
        remark: $('#p-remark').value.trim(),
      }});
      showToast('收款成功，权益已入账（' + r.product_name + '）');
      route();
    } catch (err) { showToast(err.message, true); }
  });
}

/* ================= 商品管理 ================= */
async function renderProducts(el) {
  const products = await API.request('/api/payments/products');
  el.innerHTML = `
    <div class="toolbar"><div class="spacer"></div><button class="btn btn-primary" id="pr-add">+ 新增商品</button></div>
    <div class="card"><div class="table-wrap"><table class="tbl">
      <thead><tr><th>编码</th><th>名称</th><th>类型</th><th class="num">课时</th><th class="num">售价</th><th>有效期</th><th>跨店</th></tr></thead>
      <tbody>${products.map((p) => `<tr><td>${esc(p.code)}</td><td>${esc(p.name)}</td><td>${statusTag('ptype', p.ptype)}</td><td class="num">${p.total_sessions || '—'}</td><td class="num">${fmtCents(p.price_cents)}</td><td>${p.valid_days} 天</td><td>${p.cross_store ? '是' : '否'}</td></tr>`).join('') || '<tr><td colspan="7" class="empty">暂无商品</td></tr>'}</tbody>
    </table></div></div>`;

  $('#pr-add').addEventListener('click', () => {
    const mask = openModal(`
      <h3>新增商品</h3>
      <div class="form-grid">
        <div class="field"><label>名称</label><input id="g-name" placeholder="如：20 节私教包"></div>
        <div class="field"><label>类型</label><select id="g-type"><option value="PACKAGE">课程包</option><option value="PRIVATE">私教</option><option value="STORED">储值卡</option></select></div>
        <div class="field"><label>总课时</label><input id="g-sessions" type="number" value="10"></div>
        <div class="field"><label>售价（元）</label><input id="g-price" type="number" step="0.01"></div>
        <div class="field"><label>有效期（天）</label><input id="g-days" type="number" value="365"></div>
        <div class="field"><label>跨店通用</label><select id="g-cross"><option value="1">是</option><option value="0">否</option></select></div>
      </div>
      <div class="form-actions"><button class="btn btn-secondary" onclick="this.closest('.modal-mask').remove()">取消</button>
      <button class="btn btn-primary" id="g-submit">保存</button></div>`);
    mask.querySelector('#g-submit').addEventListener('click', async () => {
      try {
        await API.request('/api/payments/products', { method: 'POST', body: {
          name: $('#g-name').value.trim(), ptype: $('#g-type').value,
          total_sessions: Number($('#g-sessions').value), price_cents: yuanToCents($('#g-price').value),
          valid_days: Number($('#g-days').value), cross_store: $('#g-cross').value === '1',
        }});
        mask.remove(); showToast('商品已创建'); route();
      } catch (err) { showToast(err.message, true); }
    });
  });
}

/* ================= 排课管理 ================= */
async function renderSchedule(el) {
  const [templates, sessions, coaches] = await Promise.all([
    API.request('/api/schedule/templates'),
    API.request('/api/schedule/sessions'),
    API.request('/api/tenant/coaches'),
  ]);
  const coachName = (id) => (coaches.find((c) => c.id === id) || {}).real_name || '—';
  el.innerHTML = `
    <div class="toolbar"><div class="spacer"></div><button class="btn btn-primary" id="sc-add">+ 新增排期</button></div>
    <div class="card">
      <div class="card-title">排期列表</div>
      <div class="table-wrap"><table class="tbl">
        <thead><tr><th>课程</th><th>教练</th><th>开始时间</th><th class="num">名额</th><th class="num">已约</th><th>状态</th><th></th></tr></thead>
        <tbody>${sessions.map((s) => `<tr><td>${esc(s.course_name)}</td><td>${esc(coachName(s.coach_id))}</td><td>${esc(s.starts_at)}</td><td class="num">${s.capacity}</td><td class="num">${s.booked}</td><td>${statusTag('session', s.status)}</td><td class="actions">${s.status !== 'CANCELLED' ? `<button class="btn btn-secondary btn-sm" data-cancel="${s.id}">取消</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="7" class="empty">暂无排期</td></tr>'}</tbody>
      </table></div>
    </div>`;

  document.querySelectorAll('[data-cancel]').forEach((b) =>
    b.addEventListener('click', async () => {
      try { await API.request('/api/schedule/sessions/' + b.dataset.cancel + '/cancel', { method: 'POST' }); showToast('已取消'); route(); }
      catch (err) { showToast(err.message, true); }
    }));

  $('#sc-add').addEventListener('click', () => {
    const mask = openModal(`
      <h3>新增排期</h3>
      <div class="form-grid">
        <div class="field full"><label>课程模板</label><select id="s-tpl">${templates.map((t) => `<option value="${t.id}">${esc(t.name)} · ${esc(coachName(t.coach_id))} · 容量 ${t.capacity}</option>`).join('')}</select></div>
        <div class="field full"><label>开始时间（YYYY-MM-DD HH:MM）</label><input id="s-time" placeholder="2026-11-11 10:00"></div>
      </div>
      <div class="form-actions"><button class="btn btn-secondary" onclick="this.closest('.modal-mask').remove()">取消</button>
      <button class="btn btn-primary" id="s-submit">保存</button></div>`);
    if (!templates.length) { mask.querySelector('#s-tpl').innerHTML = '<option value="">暂无课程模板，请先建模板</option>'; }
    mask.querySelector('#s-submit').addEventListener('click', async () => {
      try {
        await API.request('/api/schedule/sessions', { method: 'POST', body: { template_id: $('#s-tpl').value, starts_at: $('#s-time').value.trim() } });
        mask.remove(); showToast('排期已创建'); route();
      } catch (err) { showToast(err.message, true); }
    });
  });
}

/* ================= 账实核对 ================= */
async function renderReconcile(el) {
  const [stores, diffs] = await Promise.all([
    API.request('/api/tenant/stores'),
    API.request('/api/dashboard/diffs'),
  ]);
  const today = new Date().toISOString().slice(0, 10);
  el.innerHTML = `
    <div class="card">
      <div class="card-title">日终账实核对 <span class="hint">按门店生成台账，检测负余额类数据异常</span></div>
      <div class="form-grid">
        <div class="field"><label>门店</label><select id="r-store">${stores.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select></div>
        <div class="field"><label>日期</label><input id="r-date" type="date" value="${today}"></div>
      </div>
      <div class="form-actions"><button class="btn btn-primary" id="r-run">生成台账并核对</button></div>
      <div id="r-result" style="margin-top:8px;font-size:14px"></div>
    </div>
    <div class="card">
      <div class="card-title">差异挂起</div>
      ${diffs.length ? `<div class="table-wrap"><table class="tbl"><thead><tr><th>日期</th><th>类型</th><th>说明</th><th>状态</th><th></th></tr></thead>
      <tbody>${diffs.map((d) => `<tr><td>${esc(d.biz_date)}</td><td>${statusTag('enroll', d.status === 'PENDING' ? 'NO_SHOW' : 'ATTENDED')}</td><td>${esc(d.description)}</td><td>${esc(d.status)}</td><td class="actions">${d.status === 'PENDING' ? `<button class="btn btn-secondary btn-sm" data-resolve="${d.id}">处理</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`
      : '<div class="empty">暂无挂起差异</div>'}
    </div>`;

  $('#r-run').addEventListener('click', async () => {
    try {
      const r = await API.request('/api/dashboard/reconcile', { method: 'POST', body: { store_id: $('#r-store').value, date: $('#r-date').value } });
      $('#r-result').innerHTML = `<div style="padding:12px;background:var(--paper-2);border-radius:8px">
        收款 <b>${r.purchase_count}</b> 笔 · 金额 <b>${fmtCents(r.purchase_cents)}</b> · 消课 <b>${r.consume_sessions}</b> 节 · 新发现差异 <b>${r.new_diffs}</b> 笔</div>`;
      route();
    } catch (err) { showToast(err.message, true); }
  });

  document.querySelectorAll('[data-resolve]').forEach((b) =>
    b.addEventListener('click', () => {
      const mask = openModal(`<h3>处理差异</h3><div class="field"><label>处理说明</label><textarea id="d-note" rows="3" placeholder="核查结论…"></textarea></div>
        <div class="form-actions"><button class="btn btn-secondary" onclick="this.closest('.modal-mask').remove()">取消</button><button class="btn btn-primary" id="d-submit">确认处理</button></div>`);
      mask.querySelector('#d-submit').addEventListener('click', async () => {
        try { await API.request('/api/dashboard/diffs/' + b.dataset.resolve + '/resolve', { method: 'POST', body: { note: $('#d-note').value.trim() } }); mask.remove(); showToast('已处理'); route(); }
        catch (err) { showToast(err.message, true); }
      });
    }));
}

/* ================= 启动 ================= */
if (API.token) { route(); } else { showLogin(); }
