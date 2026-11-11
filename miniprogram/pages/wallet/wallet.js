const api = require('../../utils/api');

Page({
  data: {
    member: null,
    entitlements: [],
    bookings: [],
    ledger: [],
    tab: 'entitlements',
  },

  onShow() {
    this.loadData();
  },

  async loadData() {
    try {
      const [me, ents, bookings, ledger] = await Promise.all([
        api.request('member', '/api/app/me'),
        api.request('member', '/api/app/entitlements'),
        api.request('member', '/api/app/bookings'),
        api.request('member', '/api/app/ledger'),
      ]);
      this.setData({ member: me, entitlements: ents, bookings, ledger });
    } catch (e) {
      wx.showToast({ title: e.message, icon: 'none' });
    }
  },

  switchTab(e) {
    this.setData({ tab: e.currentTarget.dataset.t });
  },

  goCoach() {
    wx.navigateTo({ url: '/pages/coach/coach' });
  },
});
