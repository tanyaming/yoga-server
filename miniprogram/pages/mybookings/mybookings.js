const api = require('../../utils/api');

const STATUS = {
  BOOKED:    { cls: 'tag-success', text: '已预约' },
  WAITLIST:  { cls: 'tag-mist',    text: '候补中' },
  CANCELLED: { cls: 'tag-neutral', text: '已取消' },
  ATTENDED:  { cls: 'tag-success', text: '已到课' },
  NO_SHOW:   { cls: 'tag-clay',    text: '爽约' },
};

function parseLocal(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(s || '');
  if (!m) return 0;
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]).getTime();
}

Page({
  data: {
    upcoming: [],
    history: [],
    shown: [],
    tab: 'upcoming',
    emptyText: '暂无待上课的预约',
    loading: true,
  },

  onShow() { this.loadData(); },

  async loadData() {
    try {
      const rows = await api.request('member', '/api/app/bookings');
      const now = Date.now();
      const all = (rows || []).map((b) => {
        const m = STATUS[b.status] || { cls: 'tag-neutral', text: b.status };
        const t = parseLocal(b.starts_at);
        const pending = b.status === 'BOOKED' || b.status === 'WAITLIST';
        return Object.assign({}, b, {
          tagCls: m.cls, statusText: m.text,
          hh: (b.starts_at || '').slice(11, 16),
          md: (b.starts_at || '').slice(5, 10),
          dateKey: (b.starts_at || '').slice(0, 10),
          isFuture: t > now && pending,
          canCancel: t > now && pending,
        });
      });
      const upcoming = all.filter((b) => b.isFuture);
      const history = all.filter((b) => !b.isFuture);
      const tab = this.data.tab;
      this.setData({
        upcoming, history,
        shown: tab === 'upcoming' ? upcoming : history,
        emptyText: tab === 'upcoming' ? '暂无待上课的预约' : '暂无历史记录',
        loading: false,
      });
    } catch (e) {
      this.setData({ loading: false });
      wx.showToast({ title: e.message, icon: 'none' });
    }
  },

  onTab(e) {
    const k = e.currentTarget.dataset.k;
    this.setData({
      tab: k,
      shown: k === 'upcoming' ? this.data.upcoming : this.data.history,
      emptyText: k === 'upcoming' ? '暂无待上课的预约' : '暂无历史记录',
    });
  },

  goDetail(e) {
    const { sid, date } = e.currentTarget.dataset;
    if (!sid) return;
    wx.navigateTo({ url: '/pages/booking/booking?session_id=' + sid + '&date=' + encodeURIComponent(date || '') });
  },

  async cancel(e) {
    try {
      await api.request('member', '/api/app/cancel', {
        method: 'POST', data: { enrollment_id: e.currentTarget.dataset.id },
      });
      wx.showToast({ title: '已取消预约', icon: 'success' });
      this.loadData();
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
  },

  goBooking() { wx.switchTab({ url: '/pages/index/index' }); },
});
