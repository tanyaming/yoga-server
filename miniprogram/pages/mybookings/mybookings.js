const api = require('../../utils/api');

const STATUS = {
  BOOKED:    { cls: 'tag-success', text: '已预约' },
  WAITLIST:  { cls: 'tag-mist',    text: '候补中' },
  OFFERED:   { cls: 'tag-lime',    text: '递补待确认' },
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
    reviewedIds: [],
  },

  onShow() { this.loadData(); },

  async loadData() {
    try {
      const rows = await api.request('member', '/api/app/bookings');
      const now = Date.now();
      const reviewed = this.data.reviewedIds;
      const all = (rows || []).map((b) => {
        const m = STATUS[b.status] || { cls: 'tag-neutral', text: b.status };
        const t = parseLocal(b.starts_at);
        const pending = b.status === 'BOOKED' || b.status === 'WAITLIST' || b.status === 'OFFERED';
        return Object.assign({}, b, {
          tagCls: m.cls, statusText: m.text,
          hh: (b.starts_at || '').slice(11, 16),
          md: (b.starts_at || '').slice(5, 10),
          dateKey: (b.starts_at || '').slice(0, 10),
          isFuture: t > now && pending,
          canCancel: t > now && pending,
          canConfirm: b.status === 'OFFERED',
          canReview: b.status === 'ATTENDED' && reviewed.indexOf(b.session_id) < 0,
          offerHint: b.status === 'OFFERED' && b.offer_expires_at
            ? '请在 ' + b.offer_expires_at.slice(11, 16) + ' 前确认' : '',
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

  // FR-14：候补递补后 15 分钟锁定窗口内确认，OFFERED → BOOKED
  async confirmOffer(e) {
    const id = e.currentTarget.dataset.id;
    try {
      await api.request('member', '/api/app/offers/' + id + '/confirm', {
        method: 'POST', data: {},
      });
      wx.showToast({ title: '递补已确认', icon: 'success' });
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
    this.loadData();  // 超时顺延的情况也刷新，展示最新状态
  },

  // FR-20：对已到课课程评分 + 文字评价，提交后进入审核（先审后显）
  async review(e) {
    const sid = e.currentTarget.dataset.sid;
    let rating = 0;
    try {
      rating = await new Promise((resolve, reject) => {
        wx.showActionSheet({
          itemList: ['1 星 · 待改进', '2 星', '3 星 · 一般', '4 星', '5 星 · 非常满意'],
          success: (r) => resolve(r.tapIndex + 1), fail: reject,
        });
      });
    } catch (err) { return; }
    let content = '';
    try {
      content = await new Promise((resolve, reject) => {
        wx.showModal({
          title: '评价教练', editable: true,
          placeholderText: '说说这节课的感受（请勿填写手机号等信息）',
          success: (r) => r.confirm ? resolve(r.content || '') : reject(new Error('cancel')),
          fail: reject,
        });
      });
    } catch (err) { return; }
    try {
      await api.request('member', '/api/app/reviews', {
        method: 'POST', data: { session_id: sid, rating, content },
      });
      this.setData({ reviewedIds: this.data.reviewedIds.concat([sid]) });
      wx.showToast({ title: '已提交，审核后展示', icon: 'none' });
      this.loadData();
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
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
