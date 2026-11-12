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
