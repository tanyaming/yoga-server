/* 晨蕴瑜伽 · 管理后台前端逻辑 */
"use strict";

const DEMO_ADMIN_OPENID = "admin_local_000000000000000000";
const LS_KEY = "yoga_admin_openid";
const WEEK_CHARS = ["日", "一", "二", "三", "四", "五", "六"];
const TYPE_NAMES = { 4: "团课", 1: "小班", 2: "私教" };

let adminOpenid = localStorage.getItem(LS_KEY) || "";
let basisCache = { lessons: [], teachers: [] };
let weekOffset = 0; // 0 = 本周

/* ---------------- 工具 ---------------- */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), 2200);
}

async function api(path, options) {
  const resp = await fetch(path, options);
  const text = await resp.text();
  try { return JSON.parse(text); } catch (e) { return text; }
}

/* 时间戳：date_time 为“北京时间 0 点”的 UTC 秒，+28800 后取 UTC 字段即为北京日期 */
function beijingDate(ts) {
  return new Date((ts + 28800) * 1000);
}
function fmtDate(ts) {
  const d = beijingDate(ts);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}
function fmtDateCn(ts) {
  const d = beijingDate(ts);
  return `${d.getUTCDate()}日（周${WEEK_CHARS[d.getUTCDay()]}）`;
}
function fmtTime(seconds) {
  const s = Number(seconds) || 0;
  return `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
}
function timeToSeconds(hhmm) {
  const [h, m] = String(hhmm || "0:0").split(":").map(Number);
  return (h || 0) * 3600 + (m || 0) * 60;
}
function fmtTs10(ts) {
  return new Date(Number(ts) * 1000).toLocaleString("zh-CN", { hour12: false });
}
/* 北京时间当前所在周的周一 0 点（返回 date_time 同口径的 ts） */
function mondayTs(offsetWeeks = 0) {
  const now = new Date((Date.now() / 1000 + 28800) * 1000);
  const wd = now.getUTCDay(); // 0=周日
  const back = (wd + 6) % 7; // 距周一天数
  const monday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - back + offsetWeeks * 7));
  return Math.floor(monday.getTime() / 1000) - 28800;
}

/* ---------------- 登录 ---------------- */
async function tryLogin(openid) {
  const u = await api(`/yoga/user/query?openid=${encodeURIComponent(openid)}`);
  if (!u || u.user_type !== 4) {
    $("#login-error").textContent = !u ? "该 OpenID 不存在，请先在数据库中创建 user_type=4 的管理员账号" : "该账号不是管理员（user_type ≠ 4）";
    return;
  }
  adminOpenid = openid;
  localStorage.setItem(LS_KEY, openid);
  $("#login-error").textContent = "";
  enterApp(u);
}

function enterApp(u) {
  $("#login-mask").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#admin-name").textContent = (u && u.nick_name) || "管理员";
  if (u && u.avatar_url) $("#admin-avatar").src = u.avatar_url;
  loadBasis();
  switchPage("dashboard");
  loadDashboard();
}

function logout() {
  localStorage.removeItem(LS_KEY);
  adminOpenid = "";
  location.reload();
}

/* ---------------- 页面切换 ---------------- */
function switchPage(name) {
  $$(".page").forEach((p) => p.classList.add("hidden"));
  $(`#page-${name}`).classList.remove("hidden");
  $$(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.page === name));
  if (name === "courses") loadCourses();
  if (name === "members") loadMembers();
  if (name === "schedule") { $("#schedule-img").src = `/yoga/admin/schedule?t=${Date.now()}`; }
  if (name === "basis") renderBasis();
  if (name === "dashboard") loadDashboard();
}

/* ---------------- 概览 ---------------- */
async function loadDashboard() {
  const idx = (await api("/yoga/index")) || {};
  const start = mondayTs(0);
  const lessons = (await api(`/yoga/admin/lessons?start=${start}&end=${start + 7 * 86400}`)) || [];
  const members = (await api(`/yoga/admin/users/all?open_id=${encodeURIComponent(adminOpenid)}`)) || [];

  $("#st-courses").textContent = lessons.length;
  $("#st-bookings").textContent = lessons.reduce((s, l) => s + (l.count || 0), 0);
  $("#st-members").textContent = members.length;
  $("#st-coaches").textContent = (idx.teachers || []).length;
  $("#st-lessons").textContent = basisCache.lessons.length || (idx.teachers ? basisCache.lessons.length : 0);
  $("#st-notices").textContent = (idx.notices || []).length;

  fillList("#dash-booked", (idx.booked || []).map((b) => ({ main: b.nick_name || "学员", sub: "完成了一次预约" })));
  fillList("#dash-notices", (idx.notices || []).map((n) => ({ main: n.title, sub: fmtTs10(n.updated_time) })));
  fillList("#dash-teachers", (idx.teachers || []).map((t) => ({ main: t.name, sub: (t.introduction || "").slice(0, 18) })));

  const byDay = {};
  lessons.forEach((l) => {
    const key = fmtDateCn(l.date_time);
    byDay[key] = byDay[key] || [];
    byDay[key].push(`${fmtTime(l.start_time)} ${l.lesson_name}·${l.teacher_name} (${l.count}/${l.peoples})`);
  });
  const weekItems = Object.keys(byDay).sort().map((k) => ({ main: k, sub: byDay[k].join("，") }));
  fillList("#dash-week", weekItems);
}

function fillList(sel, items) {
  const ul = $(sel);
  ul.innerHTML = "";
  if (!items.length) { ul.innerHTML = `<li class="empty">暂无数据</li>`; return; }
  items.forEach((it) => {
    const li = document.createElement("li");
    li.innerHTML = `<span>${escapeHtml(it.main)}</span><span class="muted">${escapeHtml(it.sub || "")}</span>`;
    ul.appendChild(li);
  });
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- 基础数据（课名 / 教练名列表） ---------------- */
async function loadBasis() {
  const d = (await api("/yoga/admin/lessons/and/teachers?id=0")) || {};
  basisCache.lessons = d.lessons || [];
  basisCache.teachers = d.teachers || [];
}

function renderBasis() {
  fillList("#basis-lessons", basisCache.lessons.map((n) => ({ main: n })));
  api("/yoga/index").then((idx) => {
    fillList("#basis-coaches", ((idx || {}).teachers || []).map((t) => ({ main: t.name, sub: t.introduction })));
  });
}

/* ---------------- 课程管理 ---------------- */
async function loadCourses() {
  const start = mondayTs(weekOffset);
  const end = start + 7 * 86400;
  $("#week-label").textContent = `${fmtDate(start)} ~ ${fmtDate(end - 86400)}`;

  const rows = (await api(`/yoga/admin/lessons?start=${start}&end=${end}`)) || [];
  const filter = Number($("#filter-type").value) || 0;
  const list = filter ? rows.filter((r) => r.class_type === filter) : rows;

  const tbody = $("#course-table tbody");
  tbody.innerHTML = "";
  if (!list.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty">本周暂无课程，点击右上角「批量排课」创建</td></tr>`;
    return;
  }
  list.forEach((r) => {
    const tr = document.createElement("tr");
    const statusTag = r.hidden === 1 ? `<span class="tag off">已停课</span>` : (r.count >= r.peoples ? `<span class="tag full">已满员</span>` : `<span class="tag">招生中</span>`);
    tr.innerHTML = `
      <td>${fmtDateCn(r.date_time)}</td>
      <td>${fmtTime(r.start_time)}~${fmtTime(r.end_time)}</td>
      <td>${escapeHtml(r.lesson_name)}</td>
      <td>${escapeHtml(r.teacher_name)}</td>
      <td>${TYPE_NAMES[r.class_type] || r.class_type}</td>
      <td>${r.count}/${r.peoples}</td>
      <td>${statusTag}</td>
      <td class="td-actions">
        <button class="btn small ghost" data-act="toggle" data-id="${r.course_id}" data-hidden="${r.hidden}">${r.hidden === 1 ? "恢复" : "停课"}</button>
        <button class="btn small ghost" data-act="edit" data-id="${r.course_id}" data-start="${r.start_time}" data-type="${r.class_type}">系列修改</button>
        <button class="btn small danger-text" data-act="del" data-id="${r.course_id}">删除</button>
      </td>`;
    tbody.appendChild(tr);
  });
}

async function courseAction(act, btn) {
  const id = btn.dataset.id;
  if (act === "toggle") {
    const target = btn.dataset.hidden === "1" ? 0 : 1;
    const r = await api(`/yoga/admin/lesson/hidden?id=${id}&status=${target}`);
    if (String(r) === String(id)) { toast(target === 1 ? "已停课" : "已恢复"); loadCourses(); }
    else toast("操作失败");
  } else if (act === "del") {
    if (!confirm("确认删除该课程？将连带清理该课全部预约记录。")) return;
    const r = await api(`/yoga/admin/lesson/delete?id=${id}`);
    if (String(r) === String(id)) { toast("已删除"); loadCourses(); loadDashboard(); }
    else toast("删除失败");
  } else if (act === "edit") {
    openEditModal(Number(id), Number(btn.dataset.start), Number(btn.dataset.type));
  }
}

/* ---------------- 批量排课 ---------------- */
function fillSelect(sel, names, current) {
  sel.innerHTML = names.map((n) => `<option ${n === current ? "selected" : ""}>${escapeHtml(n)}</option>`).join("");
}

async function openBatchModal() {
  await loadBasis();
  fillSelect($("#batch-lesson"), basisCache.lessons);
  fillSelect($("#batch-teacher"), basisCache.teachers);
  $("#batch-result").textContent = "";
  $("#batch-result").className = "modal-result";
  $("#batch-modal").classList.remove("hidden");
}

async function submitBatch() {
  const body = {
    class_type: Number($("#batch-type").value),
    start_time: timeToSeconds($("#batch-start").value),
    end_time: timeToSeconds($("#batch-end").value),
    peoples: Number($("#batch-peoples").value) || 0,
    date_time: Number($("#batch-week").value), // 0=周日（与后端 PG dow 口径一致）
    lesson: $("#batch-lesson").value,
    teacher: $("#batch-teacher").value,
  };
  const r = await api("/yoga/admin/lessons/update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const box = $("#batch-result");
  const n = Number(r);
  if (n > 0) {
    box.className = "modal-result";
    box.textContent = `排课成功：未来一年共创建 ${n} 节课`;
    setTimeout(() => { $("#batch-modal").classList.add("hidden"); weekOffset = 0; loadCourses(); }, 1200);
  } else {
    box.className = "modal-result err";
    box.textContent = n === -1 ? "课程或教练不存在，请先在数据库中创建" : "排课失败";
  }
}

/* ---------------- 系列修改 ---------------- */
let editCtx = { id: 0, old_start_time: 0, old_class_type: 0 };

async function openEditModal(id, oldStart, oldType) {
  const d = (await api(`/yoga/admin/lessons/and/teachers?id=${id}`)) || {};
  const cur = d.lesson || {};
  await loadBasis();
  fillSelect($("#edit-lesson"), basisCache.lessons, cur.lesson_name);
  fillSelect($("#edit-teacher"), basisCache.teachers, cur.teacher_name);
  $("#edit-type").value = oldType === 1 ? "1" : "4";
  $("#edit-start").value = fmtTime(cur.start_time ?? oldStart);
  $("#edit-end").value = "";
  $("#edit-peoples").value = cur.peoples ?? "";
  editCtx = { id, old_start_time: oldStart, old_class_type: oldType };
  $("#edit-target-tip").textContent = `目标课程 #${id}：${cur.lesson_name || ""} · ${cur.teacher_name || ""}`;
  $("#edit-result").textContent = "";
  $("#edit-result").className = "modal-result";
  $("#edit-modal").classList.remove("hidden");
}

async function submitEdit() {
  const body = {
    id: editCtx.id,
    old_start_time: editCtx.old_start_time,
    old_class_type: editCtx.old_class_type,
    lesson: $("#edit-lesson").value,
    teacher: $("#edit-teacher").value,
    class_type: Number($("#edit-type").value),
    start_time: timeToSeconds($("#edit-start").value),
    end_time: timeToSeconds($("#edit-end").value),
    peoples: Number($("#edit-peoples").value) || 0,
  };
  const r = await api("/yoga/lesson/update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const box = $("#edit-result");
  box.className = "modal-result";
  box.textContent = "已提交，课程列表已刷新";
  setTimeout(() => { $("#edit-modal").classList.add("hidden"); loadCourses(); }, 1000);
}

/* ---------------- 会员管理 ---------------- */
let memberCache = [];

async function loadMembers() {
  const rows = (await api(`/yoga/admin/users/all?open_id=${encodeURIComponent(adminOpenid)}`)) || [];
  memberCache = rows;
  const tbody = $("#member-table tbody");
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty">暂无会员</td></tr>`;
    return;
  }
  rows.forEach((m) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><img class="avatar" src="${escapeHtml(m.avatar_url || "")}" onerror="this.style.visibility='hidden'"></td>
      <td>${m.id}</td>
      <td>${escapeHtml(m.nick_name || "-")}</td>
      <td>${fmtTs10(m.creation_time)}</td>
      <td class="td-actions"><button class="btn small ghost" data-act="detail" data-id="${m.id}">详情</button></td>`;
    tbody.appendChild(tr);
  });
}

async function openMemberDrawer(id) {
  const u = await api(`/yoga/admin/user?id=${id}`);
  const m = memberCache.find((x) => x.id === id) || {};
  if (!u) { toast("会员不存在"); return; }
  $("#drawer-title").textContent = `会员详情 · #${id}`;
  $("#drawer-avatar").src = u.avatar_url || "";
  $("#drawer-name").textContent = u.nick_name || u.name || "-";
  $("#drawer-meta").innerHTML = [
    `OpenID：${escapeHtml(u.open_id || "-")}`,
    `手机：${escapeHtml(u.phone || "-")} · 性别：${u.gender === 1 ? "男" : u.gender === 2 ? "女" : "未填"}`,
    `住址：${escapeHtml(u.address || "-")}`,
    `备注：${escapeHtml(u.note || "-")}`,
  ].join("<br>");

  const st = (await api(`/yoga/user/book/statistics?id=${encodeURIComponent(u.open_id || "")}`)) || {};
  $("#drawer-big").textContent = st.big || 0;
  $("#drawer-small").textContent = st.small || 0;
  $("#drawer-one").textContent = st.one || 0;

  const recs = (await api(`/yoga/admin/user/lessons?id=${id}&start=0&end=0`)) || [];
  const tbody = $("#drawer-records");
  tbody.innerHTML = "";
  if (!recs.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty">暂无约课记录</td></tr>`;
  } else {
    recs.forEach((r) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${fmtDate(r.date_time)}</td>
        <td>${fmtTime(r.start_time)}~${fmtTime(r.end_time)}</td>
        <td>${escapeHtml(r.lesson_name)}</td>
        <td>${escapeHtml(r.teacher_name)}</td>
        <td>${TYPE_NAMES[r.class_type] || r.class_type}</td>`;
      tbody.appendChild(tr);
    });
  }
  $("#member-drawer").classList.remove("hidden");
}

/* ---------------- 事件绑定 ---------------- */
function bind() {
  $("#login-btn").addEventListener("click", () => tryLogin($("#login-openid").value.trim()));
  $("#login-openid").addEventListener("keydown", (e) => { if (e.key === "Enter") tryLogin($("#login-openid").value.trim()); });
  $("#login-demo").addEventListener("click", () => tryLogin(DEMO_ADMIN_OPENID));
  $("#logout-btn").addEventListener("click", logout);

  $$(".nav-item").forEach((n) => n.addEventListener("click", () => switchPage(n.dataset.page)));

  $("#week-prev").addEventListener("click", () => { weekOffset--; loadCourses(); });
  $("#week-next").addEventListener("click", () => { weekOffset++; loadCourses(); });
  $("#week-this").addEventListener("click", () => { weekOffset = 0; loadCourses(); });
  $("#filter-type").addEventListener("change", loadCourses);

  $("#btn-batch").addEventListener("click", openBatchModal);
  $("#batch-submit").addEventListener("click", submitBatch);
  $("#edit-submit").addEventListener("click", submitEdit);

  $("#course-table").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (btn) courseAction(btn.dataset.act, btn);
  });

  $("#member-table").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act='detail']");
    if (btn) openMemberDrawer(Number(btn.dataset.id));
  });
  $("#drawer-close").addEventListener("click", () => $("#member-drawer").classList.add("hidden"));
  $("#member-drawer").addEventListener("click", (e) => { if (e.target.id === "member-drawer") e.target.classList.add("hidden"); });

  /* 通用关闭弹窗 */
  $$("[data-close]").forEach((b) => b.addEventListener("click", () => $(`#${b.dataset.close}`).classList.add("hidden")));
  $$(".drawer-mask").forEach((m) => m.addEventListener("click", (e) => { if (e.target === m) m.classList.add("hidden"); }));
}

/* ---------------- 启动 ---------------- */
bind();
(async function init() {
  await loadBasis();
  if (adminOpenid) {
    const u = await api(`/yoga/user/query?openid=${encodeURIComponent(adminOpenid)}`);
    if (u && u.user_type === 4) { enterApp(u); return; }
  }
  $("#login-mask").classList.remove("hidden");
})();
