const api = require('../../utils/api');

// 与首页一致：手动解析服务端 'YYYY-MM-DD HH:MM'，避免 iOS Date 解析差异
function parseLocal(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(s || '');
  if (!m) return 0;
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]).getTime();
}

// 底部 CTA 五态（含边界态），酸柠只出现在「确认约课 / 加入候补」这一处主 CTA
const CTA = {
  OPEN:       { text: '确认约课',   cls: 'btn-primary',   disabled: false },
  FULL:       { text: '加入候补',   cls: 'btn-primary',   disabled: false },
  NO_BALANCE: { text: '去续卡 →',   cls: 'btn-secondary', disabled: false },
  BOOKED:     { text: '取消预约',   cls: 'btn-secondary', disabled: false },
  WAITLIST:   { text: '取消候补',   cls: 'btn-secondary', disabled: false },
  CLOSED:     { text: '已截止预约', cls: 'btn-disabled',  disabled: true },
  FINISHED:   { text: '课程已结束', cls: 'btn-disabled',  disabled: true },
  CANCELLED:  { text: '课程已取消', cls: 'btn-disabled',  disabled: true },
  NOT_FOUND:  { text: '返回课程表', cls: 'btn-secondary', disabled: false },
};
const STATE_TEXT = {
  OPEN: '可直接预约', FULL: '已满员 · 可候补', NO_BALANCE: '课时不足',
  BOOKED: '已预约', WAITLIST: '候补中', CLOSED: '已截止',
  FINISHED: '已结束', CANCELLED: '已取消', NOT_FOUND: '课程不存在',
};
const WARN = {
  NO_BALANCE: '课时不足，请先到前台续卡后再约课（本系统无线上支付）',
  CLOSED: '已超过开课前的预约截止时间，无法再预约',
  FINISHED: '课程已开始，无法预约',
  CANCELLED: '该课程已由门店取消',
  FULL: '本节已满，加入候补后有空位将按顺序自动递补',
};

Page({
  data: {
    sessionId: '', date: '',
    session: null,
    ents: [],
    remain: 0,
    cost: 1,
    deadlineHours: 2,
    state: 'OPEN',
    stateText: '',
    cta: CTA.OPEN,
    warn: '',
    result: '',
    loading: true,
    submitting: false,
  },

  onLoad(options) {
    this.setData({ sessionId: options.session_id || '', date: options.date || '' });
  },

  onShow() { this.loadDetail(false); },

  async loadDetail(keepResult) {
    try {
      const qs = this.data.date ? '?date=' + encodeURIComponent(this.data.date) : '';
      const [sessions, me] = await Promise.all([
        api.request('member', '/api/app/sessions' + qs),
        api.request('member', '/api/app/me'),
      ]);
      const s = (sessions || []).find((x) => x.id === this.data.sessionId) || null;
      const ents = (me.entitlements || []).filter((e) => e.status === 'ACTIVE').map((e) =>
        Object.assign({}, e, {
          expireShort: (e.expires_at || '').slice(0, 10) || '长期有效',
          balText: e.ptype === 'STORED'
            ? ((e.balance_cents || 0) / 100).toFixed(2) + ' 元'
            : (e.remain_sessions || 0) + ' 节',
        }));
      const remain = me.remain_sessions || 0;
      this.applyState(s, ents, remain, keepResult);
    } catch (e) {
      this.setData({ loading: false });
      wx.showToast({ title: e.message, icon: 'none' });
    }
  },

  applyState(s, ents, remain, keepResult) {
    if (!s) {
      this.setData({
        session: null, ents, remain, state: 'NOT_FOUND',
        stateText: STATE_TEXT.NOT_FOUND, cta: CTA.NOT_FOUND, warn: '',
        result: keepResult ? this.data.result : '', loading: false,
      });
      return;
    }
    const now = Date.now();
    const start = parseLocal(s.starts_at);
    const deadlineHours = s.cancel_deadline_hours || 2;
    const deadline = start - deadlineHours * 3600 * 1000;
    const full = (s.booked >= s.capacity) || s.status === 'FULL';
    const my = s.my_status || '';
    let state = 'OPEN';
    if (s.status === 'CANCELLED') state = 'CANCELLED';
    else if (my === 'BOOKED') state = 'BOOKED';
    else if (my === 'WAITLIST') state = 'WAITLIST';
    else if (start && start <= now) state = 'FINISHED';
    else if (now >= deadline) state = 'CLOSED';
    else if (remain <= 0) state = 'NO_BALANCE';
    else if (full) state = 'FULL';
    this.setData({
      session: s, ents, remain,
      cost: s.cost_sessions || 1,
      deadlineHours,
      state,
      stateText: STATE_TEXT[state],
      cta: CTA[state],
      warn: WARN[state] || '',
      result: keepResult ? this.data.result : '',
      loading: false,
    });
  },

  onCta() {
    const { cta, submitting, state } = this.data;
    if (cta.disabled || submitting) return;
    if (state === 'NOT_FOUND') return this.goBack();
    if (state === 'BOOKED' || state === 'WAITLIST') return this.confirmCancel();
    if (state === 'NO_BALANCE') {
      wx.showToast({ title: '请到前台续卡，本系统无线上支付', icon: 'none' });
      return;
    }
    if (state === 'OPEN' || state === 'FULL') return this.confirmBook();
  },

  async confirmBook() {
    this.setData({ submitting: true, result: '' });
    try {
      const r = await api.request('member', '/api/app/book', {
        method: 'POST', data: { session_id: this.data.sessionId },
      });
      this.setData({
        result: r.status === 'BOOKED' ? '预约成功，期待你的到来' : '已加入候补，有空位将按顺序递补',
      });
      await this.loadDetail(true);
    } catch (e) {
      this.setData({ result: '⚠ ' + e.message });
    } finally {
      this.setData({ submitting: false });
    }
  },

  async confirmCancel() {
    try {
      const bookings = await api.request('member', '/api/app/bookings');
      const enr = (bookings || []).find((x) =>
        x.session_id === this.data.sessionId && (x.status === 'BOOKED' || x.status === 'WAITLIST'));
      if (!enr) { wx.showToast({ title: '无待取消预约', icon: 'none' }); return; }
      await api.request('member', '/api/app/cancel', {
        method: 'POST', data: { enrollment_id: enr.id },
      });
      wx.showToast({ title: '已取消预约', icon: 'success' });
      await this.loadDetail(false);
    } catch (e) {
      wx.showToast({ title: e.message, icon: 'none' });
    }
  },

  goBack() { wx.navigateBack({ delta: 1 }); },
});
