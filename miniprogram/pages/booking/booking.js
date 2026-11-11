const api = require('../../utils/api');

Page({
  data: {
    session: null,
    myStatus: '',
    submitting: false,
    result: '',
  },

  onLoad(options) {
    this.sessionId = options.session_id;
  },

  async onShow() {
    await this.loadDetail();
  },

  async loadDetail() {
    try {
      const sessions = await api.request('member', '/api/app/sessions');
      const s = sessions.find((x) => x.id === this.sessionId);
      this.setData({
        session: s,
        myStatus: s ? s.my_status : '',
        result: '',
      });
    } catch (e) {
      wx.showToast({ title: e.message, icon: 'none' });
    }
  },

  async confirmBook() {
    if (this.data.submitting) return;
    this.setData({ submitting: true, result: '' });
    try {
      const r = await api.request('member', '/api/app/book', {
        method: 'POST', data: { session_id: this.sessionId },
      });
      this.setData({
        result: r.status === 'BOOKED' ? '预约成功，期待你的到来' : '已加入候补，有空位将自动转正',
        myStatus: r.status,
      });
    } catch (e) {
      this.setData({ result: '⚠ ' + e.message });
    } finally {
      this.setData({ submitting: false });
      this.loadDetail();
    }
  },

  async confirmCancel() {
    const b = this.data.session;
    // 从预约列表找 enrollment id
    try {
      const bookings = await api.request('member', '/api/app/bookings');
      const enr = bookings.find((x) => x.session_id === this.sessionId && (x.status === 'BOOKED' || x.status === 'WAITLIST'));
      if (!enr) { wx.showToast({ title: '无待取消预约', icon: 'none' }); return; }
      await api.request('member', '/api/app/cancel', { method: 'POST', data: { enrollment_id: enr.id } });
      wx.showToast({ title: '已取消预约', icon: 'success' });
      this.loadDetail();
    } catch (e) {
      wx.showToast({ title: e.message, icon: 'none' });
    }
  },
});
