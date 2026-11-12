const api = require('../../utils/api');
const config = require('../../utils/config');

Page({
  data: {
    member: null,
    showCoachEntry: config.SHOW_COACH_ENTRY,
  },

  onShow() { this.loadData(); },

  async loadData() {
    try {
      const me = await api.request('member', '/api/app/me');
      this.setData({ member: me });
    } catch (e) {
      wx.showToast({ title: e.message, icon: 'none' });
    }
  },

  goEntitlements() { wx.switchTab({ url: '/pages/entitlements/entitlements' }); },
  goMyBookings() { wx.switchTab({ url: '/pages/mybookings/mybookings' }); },
  goBooking() { wx.switchTab({ url: '/pages/index/index' }); },
  goCoach() { wx.navigateTo({ url: '/pages/coach/coach' }); },
});
