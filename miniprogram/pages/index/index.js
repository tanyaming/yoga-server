const api = require('../../utils/api');

Page({
  data: {
    member: null,
    remainSessions: 0,
    sessions: [],
    loading: true,
    showBind: false,
    bindPhone: '13800000001',
    bindName: '林晓',
    bindErr: '',
  },

  onShow() {
    this.ensureLoginAndLoad();
  },

  async ensureLoginAndLoad() {
    if (api.getToken('member')) {
      await this.loadData();
      return;
    }
    try {
      const r = await api.memberLogin();
      if (r.need_bind) {
        this.setData({ showBind: true });
      } else {
        api.setToken('member', r.token);
        await this.loadData();
      }
    } catch (e) {
      this.setData({ loading: false });
      wx.showToast({ title: e.message, icon: 'none' });
    }
  },

  async loadData() {
    try {
      const me = await api.request('member', '/api/app/me');
      const sessions = await api.request('member', '/api/app/sessions');
      this.setData({
        member: me, remainSessions: me.remain_sessions, sessions, loading: false,
      });
    } catch (e) {
      this.setData({ loading: false });
      wx.showToast({ title: e.message, icon: 'none' });
    }
  },

  onBindInput(e) {
    this.setData({ [e.currentTarget.dataset.k]: e.detail.value });
  },

  async submitBind() {
    const { bindPhone, bindName } = this.data;
    if (!bindPhone || !bindName) {
      this.setData({ bindErr: '请填写手机号和姓名' });
      return;
    }
    try {
      await api.memberBind(bindPhone, bindName);
      this.setData({ showBind: false });
      await this.loadData();
    } catch (e) {
      this.setData({ bindErr: e.message });
    }
  },

  goBooking(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/booking/booking?session_id=' + id });
  },
});
