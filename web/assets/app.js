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

/* 日期工具：一律走本地时间拼串，避免 toISOString() 的 UTC 偏移把日期挪一天 */
const isoDate = (d) => d.getFullYear() + '-' +
  String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
const todayISO = () => isoDate(new Date());
function nextMondayISO() {
  const d = new Date();
  const add = ((1 - d.getDay() + 7) % 7) || 7;  // 今天即周一也取下一个周一
  d.setDate(d.getDate() + add);
  return isoDate(d);
}

const ROLE_LABEL = { PLATFORM_ADMIN: '平台管理员', TENANT_ADMIN: '馆主', STORE_MANAGER: '店长', COACH: '教练' };
const roleLabel = (r) => ROLE_LABEL[r] || r || '—';

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
    status: { ACTIVE: ['green', '启用'], DISABLED: ['gray', '停用'] },
    ctype: { GROUP: ['mist', '团课'], PRIVATE: ['clay', '私教'] },
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
  $('#sidebar-user').textContent = (user.real_name || user.username) + ' · ' + roleLabel(user.role);
  $('#topbar-user').textContent = user.real_name || user.username;
  $('#topbar-role').textContent = roleLabel(user.role);
  renderNav();
}

/** T-27：侧栏按角色只渲染有权限的入口（没有权限的入口直接不出现）。 */
function renderNav() {
  document.querySelectorAll('#sidebar-nav a').forEach((a) => {
    const roles = PAGE_ROLES[a.dataset.page] || [];
    const visible = !!currentUser && roles.indexOf(currentUser.role) >= 0;
    a.style.display = visible ? '' : 'none';
  });
}

/** 刷新页面后 sessionStorage 只剩 token：用 /api/auth/me 还原角色，否则侧栏与路由无权判断 */
async function restoreSession() {
  const user = await API.request('/api/auth/me');
  showApp(user);
  return user;
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
  products: '商品管理', schedule: '排课管理', templates: '课程模板',
  stores: '门店管理', users: '账号与角色', reconcile: '账实核对',
};
const ROUTE_NAMES = { dashboard: '经营看板', members: '会员', payments: '收款登记', products: '商品', schedule: '排课', reconcile: '账实核对' };

/* T-27 角色可见性：单一事实来源，侧栏与路由守卫都读它 */
const PAGE_ROLES = {
  dashboard: ['TENANT_ADMIN', 'STORE_MANAGER'],
  members: ['TENANT_ADMIN', 'STORE_MANAGER'],
  payments: ['TENANT_ADMIN', 'STORE_MANAGER'],
  products: ['TENANT_ADMIN'],
  schedule: ['TENANT_ADMIN', 'STORE_MANAGER', 'COACH'],
  templates: ['TENANT_ADMIN'],
  stores: ['TENANT_ADMIN'],
  users: ['TENANT_ADMIN'],
  reconcile: ['TENANT_ADMIN', 'STORE_MANAGER'],
};

function canView(page) {
  const roles = PAGE_ROLES[page];
  if (!roles) return false;
  return !!currentUser && roles.indexOf(currentUser.role) >= 0;
}

/**
 * 让侧栏当前项完整落在可视区内。
 *
 * 为什么不直接 scrollIntoView({block:'nearest'})：浏览器会连带滚动**所有**可滚动祖先，
 * 页面本身也能滚，切页时主内容区会被莫名顶动一下。这里只算 nav 容器需要的 scrollTop，
 * 副作用为零，也能保证高亮块完整露出（1080×500 侧栏 9 项必须滚动）。
 * 上下各留 4px 余量，避免高亮块紧贴容器边缘看着像被切掉。
 */
function keepNavItemVisible() {
  const nav = $('#sidebar-nav');
  const active = nav && nav.querySelector('a.on');
  if (!nav || !active) return;
  const navRect = nav.getBoundingClientRect();
  const itemRect = active.getBoundingClientRect();
  const styles = window.getComputedStyle(nav);
  // 可视区边界去掉 padding：这样滚到顶/底时仍保留呼吸感，不会贴住容器边缘
  const topEdge = navRect.top + (parseFloat(styles.paddingTop) || 0);
  const bottomEdge = navRect.bottom - (parseFloat(styles.paddingBottom) || 0);
  if (itemRect.bottom > bottomEdge) {
    nav.scrollTop += itemRect.bottom - bottomEdge;
  } else if (itemRect.top < topEdge) {
    nav.scrollTop -= topEdge - itemRect.top;
  }
}

async function route() {
  if (!API.token) { showLogin(); return; }
  if (!currentUser) {
    try { await restoreSession(); } catch (err) { showLogin(); return; }
  }
  const page = (location.hash || '#/dashboard').replace('#/', '') || 'dashboard';
  $('#topbar-title').textContent = PAGES[page] || '经营看板';
  document.querySelectorAll('#sidebar-nav a').forEach((a) =>
    a.classList.toggle('on', a.dataset.page === page));
  keepNavItemVisible();
  const content = $('#content');
  // 直接改 hash 进入无权限页面：渲染空态，一个请求都不发
  if (!canView(page)) {
    $('#topbar-title').textContent = '无权访问';
    content.innerHTML = '<div class="empty">无权访问<br>当前角色（' + esc(roleLabel(currentUser.role)) +
      '）没有查看该页面的权限</div>';
    return;
  }
  content.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const render = { dashboard: renderDashboard, members: renderMembers, payments: renderPayments,
                     products: renderProducts, schedule: renderSchedule, templates: renderTemplates,
                     stores: renderStores, users: renderUsers, reconcile: renderReconcile }[page];
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
  const modal = mask.querySelector('.modal');
  // 有底部操作条的弹窗：内容包进 .modal-body 独立滚动，操作条留在滚动区外，
  // 避免小视口下按钮要滚动才可见、或悬浮时压住字段。
  const actions = modal.querySelector(':scope > .form-actions');
  if (actions) {
    const body = document.createElement('div');
    body.className = 'modal-body';
    while (modal.firstChild && modal.firstChild !== actions) {
      body.appendChild(modal.firstChild);
    }
    modal.insertBefore(body, actions);
  } else {
    const body = document.createElement('div');
    body.className = 'modal-body';
    while (modal.firstChild) body.appendChild(modal.firstChild);
    modal.appendChild(body);
  }
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
      <div class="kpi"><div class="k-label">本月收入</div><div class="k-value" style="font-size:22px">${fmtCents(ov.month_income_cents)}</div><div class="k-sub up">收款净额 · 已扣退款</div></div>
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
      <td>${statusTag('status', m.status)}</td>
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
    // 前端前置校验（BUG-014）：先拦明显错误，减少无效服务端往返并定位到具体字段
    const name = $('#f-name').value.trim();
    const phone = $('#f-phone').value.trim();
    if (!name) { showToast('请填写姓名', true); $('#f-name').focus(); return; }
    if (name.length > 60) { showToast('姓名过长（最多 60 字符）', true); $('#f-name').focus(); return; }
    if (!/^1[3-9]\d{9}$/.test(phone)) {
      showToast('请填写正确的 11 位手机号', true); $('#f-phone').focus(); return;
    }
    try {
      await API.request('/api/members', { method: 'POST', body: { name, phone, note: $('#f-note').value.trim() } });
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

/* ================= 门店管理（T-25） ================= */
async function renderStores(el) {
  let stores = [];
  el.innerHTML = `
    <div class="toolbar">
      <span class="form-hint">停用前需先结清该门店的未来课程</span>
      <div class="spacer"></div>
      <button class="btn btn-primary" id="st-add">+ 新建门店</button>
    </div>
    <div class="card"><div class="table-wrap"><table class="tbl">
      <thead><tr><th>门店</th><th>编码</th><th>地址</th><th>营业时间</th><th>状态</th><th class="actions">操作</th></tr></thead>
      <tbody id="st-tbody"><tr><td colspan="6" class="empty">加载中…</td></tr></tbody>
    </table></div></div>`;

  const load = async () => {
    stores = await API.request('/api/tenant/stores');
    $('#st-tbody').innerHTML = stores.length ? stores.map((s) => `<tr>
      <td>${esc(s.name)}</td>
      <td>${esc(s.code)}</td>
      <td>${esc(s.address || '—')}</td>
      <td>${esc(s.business_hours || '—')}</td>
      <td>${statusTag('status', s.status)}</td>
      <td class="actions">
        <button class="btn btn-secondary btn-sm" data-edit="${s.id}">编辑</button>
        ${s.status === 'ACTIVE' ? `<button class="btn btn-secondary btn-sm" data-disable="${s.id}">停用</button>` : ''}
      </td></tr>`).join('')
      : '<tr><td colspan="6" class="empty">暂无门店</td></tr>';

    el.querySelectorAll('[data-edit]').forEach((b) => b.addEventListener('click', () =>
      openStoreForm(stores.find((x) => x.id === b.dataset.edit), load)));
    el.querySelectorAll('[data-disable]').forEach((b) => b.addEventListener('click', async () => {
      const store = stores.find((x) => x.id === b.dataset.disable);
      if (!window.confirm('确认停用门店「' + store.name + '」？停用后不能再为该门店排课。')) return;
      try {
        await API.request('/api/tenant/stores/' + store.id + '/disable', { method: 'POST' });
        showToast('门店已停用');
        load();
      } catch (err) {
        showToast(err.message, true);  // 后端 422 文案（含「还有 N 节未结束课程」）原样展示
      }
    }));
  };

  await load();
  $('#st-add').addEventListener('click', () => openStoreForm(null, load));
}

function openStoreForm(store, done) {
  const editing = !!store;
  const s = store || { name: '', code: '', address: '', phone_mask: '', business_hours: '09:00-21:00' };
  const mask = openModal(`
    <h3>${editing ? '编辑门店' : '新建门店'}</h3>
    <div class="form-grid">
      <div class="field"><label>门店名称 *</label><input id="sf-name" value="${esc(s.name)}" placeholder="如：望京店"></div>
      <div class="field"><label>门店编码</label><input id="sf-code" value="${esc(s.code)}" placeholder="留空自动生成"></div>
      <div class="field"><label>地址</label><input id="sf-address" value="${esc(s.address)}" placeholder="街道 / 楼层"></div>
      <div class="field"><label>联系电话</label><input id="sf-phone" value="${esc(s.phone_mask || '')}" placeholder="010-****8888"></div>
      <div class="field full"><label>营业时间</label><input id="sf-hours" value="${esc(s.business_hours || '09:00-21:00')}" placeholder="09:00-21:00"></div>
    </div>
    <div class="form-actions">
      <button class="btn btn-secondary" data-cancel>取消</button>
      <button class="btn btn-primary" id="sf-submit">保存</button>
    </div>`);
  mask.querySelector('[data-cancel]').addEventListener('click', () => mask.remove());
  mask.querySelector('#sf-submit').addEventListener('click', async () => {
    const name = mask.querySelector('#sf-name').value.trim();
    if (!name) { showToast('门店名称不能为空', true); return; }
    const body = {
      name,
      code: mask.querySelector('#sf-code').value.trim(),
      address: mask.querySelector('#sf-address').value.trim(),
      phone_mask: mask.querySelector('#sf-phone').value.trim(),
      business_hours: mask.querySelector('#sf-hours').value.trim() || '09:00-21:00',
    };
    try {
      if (editing) await API.request('/api/tenant/stores/' + store.id, { method: 'PUT', body });
      else await API.request('/api/tenant/stores', { method: 'POST', body });
      mask.remove();
      showToast(editing ? '门店已更新' : '门店已创建');
      done();
    } catch (err) { showToast(err.message, true); }
  });
}

/* ================= 账号与角色（T-26） ================= */
async function renderUsers(el) {
  let stores = [];
  let users = [];
  el.innerHTML = `
    <div class="toolbar">
      <span class="form-hint">账号停用后立即无法登录，历史操作留痕不受影响</span>
      <div class="spacer"></div>
      <button class="btn btn-primary" id="us-add">+ 新建账号</button>
    </div>
    <div class="card"><div class="table-wrap"><table class="tbl">
      <thead><tr><th>账号</th><th>姓名</th><th>角色</th><th>所属门店</th><th>状态</th><th class="actions">操作</th></tr></thead>
      <tbody id="us-tbody"><tr><td colspan="6" class="empty">加载中…</td></tr></tbody>
    </table></div></div>`;

  const storeName = (id) => (stores.find((s) => s.id === id) || {}).name || '—';

  const load = async () => {
    const res = await Promise.all([API.request('/api/tenant/stores'), API.request('/api/tenant/users')]);
    stores = res[0];
    users = res[1];
    const myId = currentUser ? currentUser.id : '';
    $('#us-tbody').innerHTML = users.length ? users.map((u) => {
      const isSelf = u.id === myId;
      return `<tr>
        <td>${esc(u.username)}${isSelf ? ' <span class="tag mist">本人</span>' : ''}</td>
        <td>${esc(u.real_name || '—')}</td>
        <td>${esc(roleLabel(u.role))}</td>
        <td>${esc(u.role === 'TENANT_ADMIN' ? '全部门店' : storeName(u.store_id))}</td>
        <td>${statusTag('status', u.status)}</td>
        <td class="actions">
          <button class="btn btn-secondary btn-sm" data-reset="${u.id}">重置密码</button>
          <button class="btn btn-secondary btn-sm" data-edit="${u.id}">编辑</button>
          ${u.status === 'ACTIVE'
            ? `<button class="btn btn-secondary btn-sm" data-disable="${u.id}" ${isSelf ? 'disabled title="不能停用自己的账号"' : ''}>停用</button>`
            : ''}
        </td></tr>`;
    }).join('') : '<tr><td colspan="6" class="empty">暂无账号</td></tr>';

    el.querySelectorAll('[data-edit]').forEach((b) => b.addEventListener('click', () =>
      openUserForm(users.find((x) => x.id === b.dataset.edit), stores, load)));
    el.querySelectorAll('[data-reset]').forEach((b) => b.addEventListener('click', () =>
      openResetPassword(users.find((x) => x.id === b.dataset.reset), load)));
    el.querySelectorAll('[data-disable]').forEach((b) => b.addEventListener('click', async () => {
      if (b.disabled) { showToast('不能停用自己的账号', true); return; }
      const user = users.find((x) => x.id === b.dataset.disable);
      if (!window.confirm('确认停用账号「' + user.username + '」？停用后该账号无法登录。')) return;
      try {
        await API.request('/api/tenant/users/' + user.id + '/disable', { method: 'POST' });
        showToast('账号已停用');
        load();
      } catch (err) { showToast(err.message, true); }
    }));
  };

  await load();
  $('#us-add').addEventListener('click', () => openUserForm(null, stores, load));
}

function openUserForm(user, stores, done) {
  const editing = !!user;
  const u = user || { username: '', real_name: '', role: 'COACH', store_id: '' };
  const storeOptions = (selected) => '<option value="">未指定</option>' + stores.map((s) =>
    `<option value="${s.id}" ${s.id === selected ? 'selected' : ''}>${esc(s.name)}</option>`).join('');
  const roleOptions = (selected) => [
    ['TENANT_ADMIN', '馆主'], ['STORE_MANAGER', '店长'], ['COACH', '教练'],
  ].map(([v, label]) => `<option value="${v}" ${v === selected ? 'selected' : ''}>${label}</option>`).join('');

  const mask = openModal(`
    <h3>${editing ? '编辑账号' : '新建账号'}</h3>
    <div class="form-grid">
      ${editing ? '' : `<div class="field"><label>用户名 *</label><input id="uf-username" placeholder="登录账号，如 lisi"></div>
      <div class="field"><label>初始密码</label><input id="uf-password" value="123456"><div class="form-hint">默认 123456，请提醒首次登录后修改</div></div>`}
      <div class="field"><label>姓名</label><input id="uf-realname" value="${esc(u.real_name)}" placeholder="真实姓名"></div>
      <div class="field"><label>角色</label><select id="uf-role">${roleOptions(u.role)}</select></div>
      <div class="field"><label>所属门店</label><select id="uf-store">${storeOptions(u.store_id)}</select>
        <div class="form-hint">店长 / 教练必须指定门店</div></div>
    </div>
    <div class="form-actions">
      <button class="btn btn-secondary" data-cancel>取消</button>
      <button class="btn btn-primary" id="uf-submit">保存</button>
    </div>`);
  mask.querySelector('[data-cancel]').addEventListener('click', () => mask.remove());
  mask.querySelector('#uf-submit').addEventListener('click', async () => {
    const role = mask.querySelector('#uf-role').value;
    const storeId = mask.querySelector('#uf-store').value;
    const realName = mask.querySelector('#uf-realname').value.trim();
    if (role !== 'TENANT_ADMIN' && !storeId) { showToast('店长 / 教练必须选择所属门店', true); return; }
    try {
      if (editing) {
        await API.request('/api/tenant/users/' + user.id, {
          method: 'PATCH', body: { real_name: realName, role, store_id: storeId },
        });
        showToast('账号已更新');
      } else {
        const username = mask.querySelector('#uf-username').value.trim();
        if (!username) { showToast('用户名不能为空', true); return; }
        const password = mask.querySelector('#uf-password').value || '123456';
        if (password.length < 6) { showToast('初始密码至少 6 位', true); return; }
        await API.request('/api/tenant/users', {
          method: 'POST', body: { username, password, real_name: realName, role, store_id: storeId },
        });
        showToast('账号已创建，请提醒本人首次登录后修改密码');
      }
      mask.remove();
      done();
    } catch (err) { showToast(err.message, true); }
  });
}

function openResetPassword(user, done) {
  const mask = openModal(`
    <h3>重置密码 · ${esc(user.username)}</h3>
    <div class="field"><label>新密码（至少 6 位）</label><input id="rp-pwd" type="text" placeholder="请输入新密码">
      <div class="form-hint">重置后请告知本人，并提醒登录后自行修改</div></div>
    <div class="form-actions">
      <button class="btn btn-secondary" data-cancel>取消</button>
      <button class="btn btn-primary" id="rp-submit">确认重置</button>
    </div>`);
  mask.querySelector('[data-cancel]').addEventListener('click', () => mask.remove());
  mask.querySelector('#rp-submit').addEventListener('click', async () => {
    const pwd = mask.querySelector('#rp-pwd').value.trim();
    if (pwd.length < 6) { showToast('密码至少 6 位', true); return; }
    try {
      await API.request('/api/tenant/users/' + user.id + '/reset-password', {
        method: 'POST', body: { new_password: pwd },
      });
      mask.remove();
      showToast('密码已重置');
      done();
    } catch (err) { showToast(err.message, true); }
  });
}

/* ================= 课程模板（T-28） ================= */
const PTYPE_OPTIONS = [
  { value: 'PACKAGE', label: '课程包' },
  { value: 'PRIVATE', label: '私教' },
  { value: 'STORED', label: '储值' },
];

function ptypeText(csv) {
  const list = String(csv || '').split(',').map((x) => x.trim()).filter(Boolean);
  if (!list.length) return '<span class="tag gray">不限</span>';
  return list.map((p) => statusTag('ptype', p)).join(' ');
}

async function renderTemplates(el) {
  const res = await Promise.all([
    API.request('/api/schedule/templates'),
    API.request('/api/tenant/stores'),
    // 取全量教练用于「教练」列名称映射，下拉再按 status 过滤（停用教练不可再被选择）
    API.request('/api/tenant/coaches?include_disabled=1'),
  ]);
  const templates = res[0];
  const stores = res[1];
  const coaches = res[2];
  const storeName = (id) => (stores.find((s) => s.id === id) || {}).name || '—';
  const coachName = (id) => (coaches.find((c) => c.id === id) || {}).real_name || '待定';
  // 停用教练的模板仍要显示原名（不能静默变空），但要标注，提示需重新指派
  const coachCell = (id) => {
    const c = coaches.find((x) => x.id === id);
    if (!c) return '待定';
    return esc(c.real_name || c.username || '—') +
      (c.status === 'DISABLED' ? ' <span class="tag gray">已停用</span>' : '');
  };

  el.innerHTML = `
    <div class="toolbar">
      <span class="form-hint">修改模板只影响之后创建的课节，已排课程保持原样</span>
      <div class="spacer"></div>
      <button class="btn btn-primary" id="tp-add">+ 新建课程模板</button>
    </div>
    <div class="card"><div class="table-wrap"><table class="tbl">
      <thead><tr><th>课程名</th><th>门店</th><th>教练</th><th>课型</th><th class="num">容量</th><th class="num">时长</th><th>可约卡种</th><th class="actions">操作</th></tr></thead>
      <tbody>${templates.length ? templates.map((t) => `<tr>
        <td>${esc(t.name)}</td>
        <td>${esc(storeName(t.store_id))}</td>
        <td>${coachCell(t.coach_id)}</td>
        <td>${statusTag('ctype', t.ctype)}</td>
        <td class="num">${t.capacity}</td>
        <td class="num">${t.duration_minutes} 分钟</td>
        <td>${ptypeText(t.allowed_ptypes)}</td>
        <td class="actions"><button class="btn btn-secondary btn-sm" data-edit="${t.id}">编辑</button></td>
      </tr>`).join('') : '<tr><td colspan="8" class="empty">暂无课程模板</td></tr>'}</tbody>
    </table></div></div>`;

  const bind = () => el.querySelectorAll('[data-edit]').forEach((b) => b.addEventListener('click', () =>
    openTemplateForm(templates.find((x) => x.id === b.dataset.edit), stores, coaches, () => route())));
  bind();
  $('#tp-add').addEventListener('click', () => openTemplateForm(null, stores, coaches, () => route()));
}

function openTemplateForm(tpl, stores, coaches, done) {
  const editing = !!tpl;
  const t = tpl || { name: '', store_id: (stores[0] || {}).id || '', coach_id: '',
                     ctype: 'GROUP', capacity: 15, duration_minutes: 60, allowed_ptypes: '' };
  const selectedPtypes = String(t.allowed_ptypes || '').split(',').filter(Boolean);
  // 停用门店不再接收新模板；正在编辑的旧门店保留在列表里，避免保存时被静默改掉归属
  const storeOptions = stores.filter((s) => s.status !== 'DISABLED' || s.id === t.store_id)
    .map((s) =>
      `<option value="${s.id}" ${s.id === t.store_id ? 'selected' : ''}>${esc(s.name)}${s.status === 'DISABLED' ? '（已停用）' : ''}</option>`).join('')
    || '<option value="">暂无可用门店，请先建门店</option>';
  const coachOptions = '<option value="">待定</option>' + coaches
    .filter((c) => c.status !== 'DISABLED' || c.id === t.coach_id)
    .map((c) =>
      `<option value="${c.id}" ${c.id === t.coach_id ? 'selected' : ''}>${esc(c.real_name || c.username || c.id)}${c.status === 'DISABLED' ? '（已停用）' : ''}</option>`).join('');

  const mask = openModal(`
    <h3>${editing ? '编辑课程模板' : '新建课程模板'}</h3>
    <div class="form-grid">
      <div class="field full"><label>课程名称 *</label><input id="tf-name" value="${esc(t.name)}" placeholder="如：流瑜伽 60"></div>
      <div class="field"><label>门店</label><select id="tf-store">${storeOptions}</select></div>
      <div class="field"><label>教练</label><select id="tf-coach">${coachOptions}</select></div>
      <div class="field"><label>课型</label><select id="tf-ctype">
        <option value="GROUP" ${t.ctype === 'GROUP' ? 'selected' : ''}>团课</option>
        <option value="PRIVATE" ${t.ctype === 'PRIVATE' ? 'selected' : ''}>私教</option>
      </select></div>
      <div class="field"><label>容量（1–200）</label><input id="tf-cap" type="number" min="1" max="200" value="${t.capacity}"></div>
      <div class="field"><label>时长（15–300 分钟）</label><input id="tf-dur" type="number" min="15" max="300" value="${t.duration_minutes}"></div>
      <div class="field full"><label>可约卡种</label>
        <div class="check-group" id="tf-ptypes">${PTYPE_OPTIONS.map((p) =>
          `<label><input type="checkbox" value="${p.value}" ${selectedPtypes.indexOf(p.value) >= 0 ? 'checked' : ''}>${p.label}</label>`).join('')}</div>
        <div class="form-hint">全不选表示不限卡种</div>
      </div>
    </div>
    <div class="form-actions">
      <button class="btn btn-secondary" data-cancel>取消</button>
      <button class="btn btn-primary" id="tf-submit">保存</button>
    </div>`);
  mask.querySelector('[data-cancel]').addEventListener('click', () => mask.remove());
  mask.querySelector('#tf-submit').addEventListener('click', async () => {
    const name = mask.querySelector('#tf-name').value.trim();
    if (!name) { showToast('课程名称不能为空', true); return; }
    const capacity = Number(mask.querySelector('#tf-cap').value);
    const duration = Number(mask.querySelector('#tf-dur').value);
    if (!(capacity >= 1 && capacity <= 200)) { showToast('课程容量必须在 1–200 之间', true); return; }
    if (!(duration >= 15 && duration <= 300)) { showToast('课程时长必须在 15–300 分钟之间', true); return; }
    const ptypes = Array.from(mask.querySelectorAll('#tf-ptypes input:checked')).map((i) => i.value);
    const body = {
      name, store_id: mask.querySelector('#tf-store').value,
      coach_id: mask.querySelector('#tf-coach').value,
      ctype: mask.querySelector('#tf-ctype').value,
      capacity, duration_minutes: duration,
      allowed_ptypes: ptypes.join(','),
    };
    try {
      if (editing) {
        const r = await API.request('/api/schedule/templates/' + tpl.id, { method: 'PATCH', body });
        mask.remove();
        showToast(r.affected_future_sessions > 0
          ? '已更新，影响 ' + r.affected_future_sessions + ' 节未来课节'
          : '课程模板已更新');
      } else {
        await API.request('/api/schedule/templates', { method: 'POST', body });
        mask.remove();
        showToast('课程模板已创建');
      }
      done();
    } catch (err) { showToast(err.message, true); }
  });
}

/* ================= 排课管理（T-30 按周生成） ================= */
const WEEKLY_PRESET_TIMES = ['09:00', '10:30', '14:00', '19:00', '19:30'];
const WEEKDAY_OPTIONS = [
  { value: 1, label: '周一' }, { value: 2, label: '周二' }, { value: 3, label: '周三' },
  { value: 4, label: '周四' }, { value: 5, label: '周五' }, { value: 6, label: '周六' },
  { value: 7, label: '周日' },
];

async function renderSchedule(el) {
  const res = await Promise.all([
    API.request('/api/schedule/templates'),
    API.request('/api/tenant/stores'),
    API.request('/api/tenant/coaches?include_disabled=1'),
  ]);
  const templates = res[0];
  const stores = res[1];
  const coaches = res[2];
  const canEdit = !!currentUser && currentUser.role !== 'COACH';  // T-27：教练只读
  const coachName = (id) => (coaches.find((c) => c.id === id) || {}).real_name || '—';
  const storeName = (id) => (stores.find((s) => s.id === id) || {}).name || '—';
  // 停用门店的模板仍在列表里（历史课节要看得见），但标注出来 —— 排课会被后端 422 拦下
  const disabledStoreIds = stores.filter((s) => s.status === 'DISABLED').map((s) => s.id);
  const tplLabel = (t) => esc(t.name) +
    (disabledStoreIds.indexOf(t.store_id) >= 0 ? '（门店已停用）' : '');

  el.innerHTML = `
    <div class="toolbar">
      <input id="sc-date" type="date">
      <button class="btn btn-secondary" id="sc-filter">按日期筛选</button>
      <button class="btn btn-secondary" id="sc-reset">显示全部</button>
      <div class="spacer"></div>
      ${canEdit ? `<button class="btn btn-secondary" id="sc-weekly">按周生成</button>
      <button class="btn btn-primary" id="sc-add">+ 新增排期</button>` : '<span class="form-hint">教练视角：只读</span>'}
    </div>
    <div class="card">
      <div class="card-title">排期列表 <span class="hint" id="sc-hint">全部日期</span></div>
      <div class="table-wrap"><table class="tbl">
        <thead><tr><th>课程</th><th>门店</th><th>教练</th><th>开始时间</th><th class="num">名额</th><th class="num">已约</th><th>状态</th><th class="actions">操作</th></tr></thead>
        <tbody id="sc-tbody"><tr><td colspan="8" class="empty">加载中…</td></tr></tbody>
      </table></div>
    </div>`;

  const load = async (date) => {
    const rows = await API.request('/api/schedule/sessions' + (date ? '?date=' + encodeURIComponent(date) : ''));
    $('#sc-hint').textContent = date ? ('仅 ' + date) : '全部日期';
    $('#sc-tbody').innerHTML = rows.length ? rows.map((s) => `<tr>
      <td>${esc(s.course_name)}</td>
      <td>${esc(storeName(s.store_id))}</td>
      <td>${esc(coachName(s.coach_id))}</td>
      <td>${esc(s.starts_at)}</td>
      <td class="num">${s.capacity}</td>
      <td class="num">${s.booked}</td>
      <td>${statusTag('session', s.status)}</td>
      <td class="actions">${canEdit && s.status !== 'CANCELLED'
        ? `<button class="btn btn-secondary btn-sm" data-cancel="${s.id}">取消</button>` : '—'}</td>
    </tr>`).join('') : '<tr><td colspan="8" class="empty">该日期暂无排期</td></tr>';

    el.querySelectorAll('[data-cancel]').forEach((b) => b.addEventListener('click', async () => {
      try {
        await API.request('/api/schedule/sessions/' + b.dataset.cancel + '/cancel', { method: 'POST' });
        showToast('已取消');
        load($('#sc-date').value || '');
      } catch (err) { showToast(err.message, true); }
    }));
  };

  await load('');
  $('#sc-filter').addEventListener('click', () => load($('#sc-date').value || ''));
  $('#sc-reset').addEventListener('click', () => { $('#sc-date').value = ''; load(''); });
  if (!canEdit) return;

  $('#sc-add').addEventListener('click', () => {
    const mask = openModal(`
      <h3>新增排期</h3>
      <div class="form-grid">
        <div class="field full"><label>课程模板</label><select id="s-tpl">${templates.map((t) => `<option value="${t.id}">${tplLabel(t)} · ${esc(coachName(t.coach_id))} · 容量 ${t.capacity}</option>`).join('')}</select></div>
        <div class="field full"><label>开始时间（YYYY-MM-DD HH:MM）</label><input id="s-time" placeholder="2026-11-11 10:00"></div>
        <div class="field full"><label>预约截止（开课前小时）</label><input id="s-hours" type="number" min="0" max="72" value="2"></div>
      </div>
      <div class="form-actions"><button class="btn btn-secondary" data-cancel>取消</button>
      <button class="btn btn-primary" id="s-submit">保存</button></div>`);
    if (!templates.length) {
      mask.querySelector('#s-tpl').innerHTML = '<option value="">暂无课程模板，请先到「课程模板」创建</option>';
    }
    mask.querySelector('[data-cancel]').addEventListener('click', () => mask.remove());
    mask.querySelector('#s-submit').addEventListener('click', async () => {
      try {
        await API.request('/api/schedule/sessions', { method: 'POST', body: {
          template_id: mask.querySelector('#s-tpl').value,
          starts_at: mask.querySelector('#s-time').value.trim(),
          cancel_deadline_hours: Number(mask.querySelector('#s-hours').value || 2),
        }});
        mask.remove();
        showToast('排期已创建');
        route();
      } catch (err) { showToast(err.message, true); }
    });
  });

  $('#sc-weekly').addEventListener('click', () => openWeeklyForm(templates, stores, () => route()));
}

function openWeeklyForm(templates, stores, done) {
  // 模板所属门店可能已停用：列表里保留但标注，提交后由后端 422 拦下并原样提示
  const disabledStoreIds = stores.filter((s) => s.status === 'DISABLED').map((s) => s.id);
  const tplLabel = (t) => esc(t.name) +
    (disabledStoreIds.indexOf(t.store_id) >= 0 ? '（门店已停用）' : '');
  const mask = openModal(`
    <h3>按周生成排期</h3>
    <div class="form-grid">
      <div class="field full"><label>课程模板</label>
        <select id="w-tpl">${templates.map((t) => `<option value="${t.id}">${tplLabel(t)}</option>`).join('') || '<option value="">暂无课程模板</option>'}</select>
      </div>
      <div class="field"><label>周起始日期</label><input id="w-week" type="date" value="${nextMondayISO()}">
        <div class="form-hint">非周一将自动归到该周周一</div>
      </div>
      <div class="field"><label>预约截止（开课前小时）</label><input id="w-hours" type="number" min="0" max="72" value="2"></div>
      <div class="field full"><label>生成哪几天</label>
        <div class="check-group" id="w-days">${WEEKDAY_OPTIONS.map((d) =>
          `<label><input type="checkbox" value="${d.value}" checked>${d.label}</label>`).join('')}</div>
        <div class="form-hint">默认整周；只排周一/三/五就只勾这三天</div>
      </div>
      <div class="field full"><label>上课时段（可多选）</label>
        <div class="check-group" id="w-times">${WEEKLY_PRESET_TIMES.map((t) =>
          `<label><input type="checkbox" value="${t}" ${t === '09:00' || t === '19:00' ? 'checked' : ''}>${t}</label>`).join('')}</div>
      </div>
      <div class="field full"><label>自定义时段（HH:MM，多个用逗号分隔）</label>
        <input id="w-custom" placeholder="如 08:30, 20:00">
      </div>
    </div>
    <div class="form-hint">同一模板 + 同一时间已存在的课节会自动跳过，重复点击不会产生重复排期</div>
    <div class="form-actions">
      <button class="btn btn-secondary" data-cancel>取消</button>
      <button class="btn btn-primary" id="w-submit">生成排期</button>
    </div>`, 560);

  mask.querySelector('[data-cancel]').addEventListener('click', () => mask.remove());
  mask.querySelector('#w-submit').addEventListener('click', async () => {
    const templateId = mask.querySelector('#w-tpl').value;
    if (!templateId) { showToast('请先创建课程模板', true); return; }
    const weekStart = mask.querySelector('#w-week').value;
    if (!weekStart) { showToast('请选择周起始日期', true); return; }
    const days = Array.from(mask.querySelectorAll('#w-days input:checked')).map((i) => Number(i.value));
    if (!days.length) { showToast('请至少选择一天', true); return; }
    const times = Array.from(mask.querySelectorAll('#w-times input:checked')).map((i) => i.value);
    mask.querySelector('#w-custom').value.split(/[,，\s]+/).filter(Boolean).forEach((t) => times.push(t.trim()));
    if (!times.length) { showToast('请至少选择一个上课时段', true); return; }
    const hours = Number(mask.querySelector('#w-hours').value || 2);
    if (!(hours >= 0 && hours <= 72)) { showToast('预约截止小时必须在 0–72 之间', true); return; }
    try {
      const r = await API.request('/api/schedule/weekly', { method: 'POST', body: {
        template_id: templateId, week_start: weekStart, times, days,
        cancel_deadline_hours: hours,
      }});
      mask.remove();
      // G6：入参不是周一时，后端会吸附到该周周一，必须告诉馆主实际生效的是哪一周，
      // 否则「我明明选的周三」与列表日期对不上会被当成 bug。
      // 两段文案各自写全（不走字符串拼接）：常规文案是产品定稿措辞，QA 有静态哨兵按原文匹配。
      if (r.requested_week_start && r.week_start && r.requested_week_start !== r.week_start) {
        const md = r.week_start.slice(5).split('-');
        showToast('已按 ' + Number(md[0]) + '月' + Number(md[1]) + '日（周一）所在周生成 ' +
          r.created + ' 节，跳过 ' + r.skipped + ' 节');
      } else {
        showToast('已生成 ' + r.created + ' 节，跳过 ' + r.skipped + ' 节');
      }
      done();
    } catch (err) { showToast(err.message, true); }
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
