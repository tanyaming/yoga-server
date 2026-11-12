const api = require('../../utils/api');

Page({
  data: {
    loggedIn: false,
    username: 'coach',
    password: 'coach123',
    loginErr: '',
    today: [],
    roster: null,
    coachName: '',
  },

  onShow() {
    if (api.getToken('coach')) {
      this.setData({ loggedIn: true });
      this.loadToday();
    }
  },

  onInput(e) {
    this.setData({ [e.currentTarget.dataset.k]: e.detail.value });
  },

  async login() {
    try {
      const r = await api.request('coach', '/api/auth/login', {
        method: 'POST', data: { username: this.data.username, password: this.data.password },
      });
      api.setToken('coach', r.access_token);
      this.setData({ loggedIn: true, coachName: r.user.real_name || r.user.username, loginErr: '' });
      this.loadToday();
    } catch (e) {
      this.setData({ loginErr: e.message });
    }
  },

  async loadToday() {
    try {
      const rows = await api.request('coach', '/api/coach/today');
      // 时间拆成 HH:MM + MM-DD 两行，避免完整时间戳在窄列里折行
      const today = (rows || []).map((s) => Object.assign({}, s, {
        hh: (s.starts_at || '').slice(11, 16),
        md: (s.starts_at || '').slice(5, 10),
      }));
      this.setData({ today });
    } catch (e) {
      wx.showToast({ title: e.message, icon: 'none' });
    }
  },

  async openRoster(e) {
    const id = e.currentTarget.dataset.id;
    try {
      const roster = await api.request('coach', '/api/coach/sessions/' + id + '/roster');
      this.setData({ roster });
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
  },

  async mark(e) {
    const { mid, status } = e.currentTarget.dataset;
    const sessionId = this.data.roster.session_id;
    try {
      await api.request('coach', '/api/coach/attendance', {
        method: 'POST', data: { session_id: sessionId, member_id: mid, status },
      });
      wx.showToast({ title: status === 'ATTENDED' ? '已到课（扣 1 节）' : '已记爽约', icon: 'none' });
      this.openRoster({ currentTarget: { dataset: { id: sessionId } } });
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
  },

  // FR-17：临时到店会员登记代约待办，提示店长补录权益，不绕过权益直接点名
  async walkIn() {
    const sessionId = this.data.roster && this.data.roster.session_id;
    if (!sessionId) return;
    try {
      const members = await api.request('coach', '/api/coach/members');
      if (!members || members.length === 0) {
        wx.showToast({ title: '暂无可选会员', icon: 'none' });
        return;
      }
      const list = members.slice(0, 6);
      const pick = await new Promise((resolve, reject) => {
        wx.showActionSheet({
          itemList: list.map((m) => m.name + ' ' + m.phone_mask),
          success: (r) => resolve(list[r.tapIndex]), fail: reject,
        });
      });
      const r = await api.request('coach', '/api/coach/walk-in', {
        method: 'POST', data: { session_id: sessionId, member_id: pick.id, note: '' },
      });
      wx.showToast({ title: r.message || '已登记代约待办', icon: 'none' });
    } catch (e) {
      const msg = (e && (e.errMsg || e.message)) || '';
      if (msg.indexOf('cancel') >= 0) return;
      wx.showToast({ title: (e && e.message) || '登记失败', icon: 'none' });
    }
  },

  logout() {
    api.setToken('coach', '');
    this.setData({ loggedIn: false, today: [], roster: null });
  },

  onMaskTap() {
    this.setData({ roster: null });
  },

  noop() {},

  closeRoster() {
    this.setData({ roster: null });
  },
});
