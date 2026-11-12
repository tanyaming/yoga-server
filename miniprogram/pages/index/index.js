const api = require('../../utils/api');

const WEEK = ['日', '一', '二', '三', '四', '五', '六'];
const FILTERS = [
  { key: 'ALL', label: '全部' },
  { key: 'GROUP', label: '团课' },
  { key: 'PRIVATE', label: '私教' },
  { key: 'AVAILABLE', label: '可约' },
];

// 课程五态（PRD FR-13/14/15 + 设计规范 §5）：可约 / 已预约 / 候补 / 已满 / 已截止，
// 另含「课时不足」「已结束」「已取消」边界态。状态由服务端数据 + 本地时间共同判定。
const STATE_MAP = {
  OPEN:       { tagCls: 'tag-pine',    tagText: '可约',        actCls: 'act-go',  actText: '可约 →',   disabled: false },
  BOOKED:     { tagCls: 'tag-success', tagText: '已预约',      actCls: 'act-alt', actText: '查看',     disabled: false },
  WAITLIST:   { tagCls: 'tag-mist',    tagText: '候补中',      actCls: 'act-alt', actText: '查看',     disabled: false },
  FULL:       { tagCls: 'tag-clay',    tagText: '已满 · 可候补', actCls: 'act-go', actText: '加入候补', disabled: false },
  NO_BALANCE: { tagCls: 'tag-clay',    tagText: '课时不足',    actCls: 'act-alt', actText: '去续卡',   disabled: false },
  CLOSED:     { tagCls: 'tag-neutral', tagText: '已截止',      actCls: 'act-off', actText: '已截止',   disabled: true },
  FINISHED:   { tagCls: 'tag-neutral', tagText: '已结束',      actCls: 'act-off', actText: '已结束',   disabled: true },
  CANCELLED:  { tagCls: 'tag-neutral', tagText: '已取消',      actCls: 'act-off', actText: '已取消',   disabled: true },
};

function pad(n) { return n < 10 ? '0' + n : '' + n; }
function fmtDate(d) {
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
}
function todayStr() { return fmtDate(new Date()); }
function dayAt(offset) {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + offset);
  return d;
}
// 手动解析，避免 iOS 对 'YYYY-MM-DD HH:MM' 的 Date 解析差异
function parseLocal(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(s || '');
  if (!m) return 0;
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]).getTime();
}

Page({
  data: {
    member: null,
    remainSessions: 0,
    notice: null,
    storeName: '素序瑜伽',
    greeting: '',
    todayLabel: '',
    days: [],
    activeDate: '',
    filters: FILTERS,
    filter: 'ALL',
    listLabel: '今日排期',
    sessions: [],
    filtered: [],
    loading: true,
    emptyText: '当日暂无课程',
    showBind: false,
    bindPhone: '13800000001',
    bindName: '林晓',
    bindErr: '',
  },

  onLoad() {
    // 首项「近期」= 今天及未来；默认选中它，避免深夜/今日无课时首屏空白
    const days = [{ key: '', w: '近期', d: '→', on: true, has: false, isAll: true }];
    for (let i = 0; i < 7; i++) {
      const d = dayAt(i);
      days.push({ key: fmtDate(d), w: i === 0 ? '今' : WEEK[d.getDay()], d: pad(d.getDate()), on: false, has: false });
    }
    const now = new Date();
    this.setData({
      days,
      activeDate: '',
      todayLabel: (now.getMonth() + 1) + '月' + now.getDate() + '日 · 周' + WEEK[now.getDay()],
      greeting: now.getHours() < 12 ? '早上好' : (now.getHours() < 18 ? '下午好' : '晚上好'),
    });
  },

  onShow() { this.ensureLoginAndLoad(); },

  async ensureLoginAndLoad() {
    if (api.getToken('member')) { await this.loadData(); return; }
    try {
      const r = await api.memberLogin();
      if (r.need_bind) { this.setData({ showBind: true, loading: false }); return; }
      api.setToken('member', r.token);
      await this.loadData();
    } catch (e) {
      this.setData({ loading: false });
      wx.showToast({ title: e.message, icon: 'none' });
    }
  },

  async loadData(date) {
    // date 未传时沿用当前选中项；activeDate 为空 = 「近期」模式，不传 date
    const d = (date === undefined) ? (this.data.activeDate || '') : date;
    this.setData({ loading: true });
    try {
      // 按选中日期查询；后端同时有「今天及未来」默认下界，前端再做一层兜底，
      // 保证即使连到旧版后端也不会把历史课节显示进课程表。
      const qs = d ? '?date=' + encodeURIComponent(d) : '';
      const [me, raw] = await Promise.all([
        api.request('member', '/api/app/me'),
        api.request('member', '/api/app/sessions' + qs),
      ]);
      const list = (raw || []).filter((s) => {
        const day = (s.starts_at || '').slice(0, 10);
        return d ? day === d : day >= todayStr();
      });
      const remain = me.remain_sessions || 0;
      const decorated = list.map((s) => this.decorate(s, remain));
      this.setData({
        member: me,
        remainSessions: remain,
        notice: this.buildNotice(remain, me.entitlements || []),
        storeName: (list[0] && list[0].store_name) ? ('素序 · ' + list[0].store_name) : '素序瑜伽',
        sessions: decorated,
        listLabel: !d ? '近期排期'
          : (d === todayStr() ? '今日排期'
            : (Number(d.slice(5, 7)) + '月' + Number(d.slice(8, 10)) + '日排期')),
        loading: false,
      });
      this.applyFilter();
    } catch (e) {
      this.setData({ loading: false });
      wx.showToast({ title: e.message, icon: 'none' });
    }
  },

  decorate(s, remain) {
    const now = Date.now();
    const start = parseLocal(s.starts_at);
    const deadline = start - (s.cancel_deadline_hours || 2) * 3600 * 1000;
    const full = (s.booked >= s.capacity) || s.status === 'FULL';
    let state = 'OPEN';
    if (s.status === 'CANCELLED') state = 'CANCELLED';
    else if (s.my_status === 'BOOKED') state = 'BOOKED';
    else if (s.my_status === 'WAITLIST') state = 'WAITLIST';
    else if (start && start <= now) state = 'FINISHED';          // 已开课/已结束
    else if (now >= deadline) state = 'CLOSED';                  // 开课前 2h 截止
    else if (remain <= 0) state = 'NO_BALANCE';                  // 课时不足
    else if (full) state = 'FULL';                               // 满员 → 候补
    const m = STATE_MAP[state];
    const pct = s.capacity ? Math.min(100, Math.round((s.booked / s.capacity) * 100)) : 0;
    return Object.assign({}, s, {
      state,
      hh: (s.starts_at || '').slice(11, 16),
      md: (s.starts_at || '').slice(5, 10),
      dateKey: (s.starts_at || '').slice(0, 10),
      duration: s.duration_minutes || 60,
      ctypeLabel: s.ctype === 'PRIVATE' ? '私教' : '团课',
      costSessions: s.cost_sessions || 1,
      seatPct: pct,
      tagCls: m.tagCls, tagText: m.tagText,
      actCls: m.actCls, actText: m.actText, disabled: m.disabled,
    });
  },

  buildNotice(remain, ents) {
    let soon = '';
    (ents || []).forEach((e) => {
      if (e.status !== 'ACTIVE') return;
      const exp = (e.expires_at || '').slice(0, 10);
      if (exp && (!soon || exp < soon)) soon = exp;
    });
    const daysLeft = soon ? Math.ceil((parseLocal(soon + ' 00:00') - Date.now()) / 86400000) : 999;
    let t1;
    if (remain <= 3) t1 = '课时仅剩 ' + remain + ' 节，记得续卡';
    else if (daysLeft <= 7) t1 = '权益 ' + daysLeft + ' 天后到期';
    else t1 = '团课卡剩 ' + remain + ' 节';
    return {
      t1,
      t2: soon ? ('有效期至 ' + soon + ' · 权益账本') : '权益账本 · 无在线支付',
      urgent: remain <= 3 || daysLeft <= 7,
    };
  },

  applyFilter() {
    const f = this.data.filter;
    const list = this.data.sessions.filter((s) => {
      if (f === 'GROUP') return s.ctype === 'GROUP';
      if (f === 'PRIVATE') return s.ctype === 'PRIVATE';
      if (f === 'AVAILABLE') return s.state === 'OPEN' || s.state === 'FULL';
      return true;
    });
    const days = this.data.days.map((d) => Object.assign({}, d, {
      on: d.key === this.data.activeDate,
      has: d.key === this.data.activeDate && list.length > 0,
    }));
    this.setData({
      filtered: list,
      days,
      emptyText: f === 'AVAILABLE' ? '当前筛选下暂无可约课程'
        : (this.data.activeDate ? '当日暂无课程' : '近期暂无课程'),
    });
  },

  onDateTap(e) {
    const key = e.currentTarget.dataset.key;
    if (key === undefined || key === null || key === this.data.activeDate) return;
    this.setData({ activeDate: key, filter: 'ALL' });
    this.loadData(key);
  },

  onFilterTap(e) {
    this.setData({ filter: e.currentTarget.dataset.key });
    this.applyFilter();
  },

  goTomorrow() {
    const key = fmtDate(dayAt(1));
    this.setData({ activeDate: key, filter: 'ALL' });
    this.loadData(key);
  },

  goBooking(e) {
    const { id, state, date } = e.currentTarget.dataset;
    if (state === 'CLOSED' || state === 'FINISHED' || state === 'CANCELLED') {
      wx.showToast({ title: '该课程已不可预约', icon: 'none' });
      return;
    }
    const qs = '?session_id=' + id + (date ? '&date=' + encodeURIComponent(date) : '');
    wx.navigateTo({ url: '/pages/booking/booking' + qs });
  },

  goEntitlements() {
    wx.switchTab({ url: '/pages/entitlements/entitlements' });
  },

  onBindInput(e) { this.setData({ [e.currentTarget.dataset.k]: e.detail.value }); },

  async submitBind() {
    const { bindPhone, bindName } = this.data;
    if (!bindPhone || !bindName) { this.setData({ bindErr: '请填写手机号和姓名' }); return; }
    try {
      await api.memberBind(bindPhone, bindName);
      this.setData({ showBind: false, bindErr: '' });
      await this.loadData();
    } catch (e) {
      this.setData({ bindErr: e.message });
    }
  },
});
