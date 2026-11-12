const api = require('../../utils/api');

function daysLeft(exp) {
  if (!exp) return 999;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(exp);
  if (!m) return 999;
  return Math.ceil((new Date(+m[1], +m[2] - 1, +m[3]).getTime() - Date.now()) / 86400000);
}

Page({
  data: {
    member: null,
    remain: 0,
    ents: [],
    ledger: [],
    expiring: null,
    loading: true,
  },

  onShow() { this.loadData(); },

  async loadData() {
    try {
      const [me, ents, ledger] = await Promise.all([
        api.request('member', '/api/app/me'),
        api.request('member', '/api/app/entitlements'),
        api.request('member', '/api/app/ledger'),
      ]);
      const decorated = (ents || []).map((e, i) => {
        const left = daysLeft(e.expires_at);
        return Object.assign({}, e, {
          remainText: e.ptype === 'STORED'
            ? ((e.balance_cents || 0) / 100).toFixed(2)
            : String(e.remain_sessions || 0),
          unitText: e.ptype === 'STORED'
            ? '元储值余额' : ('节剩余 / 共 ' + e.total_sessions + ' 节'),
          expireShort: e.expires_at || '长期有效',
          statusText: e.status === 'ACTIVE' ? '有效' : (e.status === 'EXPIRED' ? '已失效' : e.status),
          statusCls: e.status === 'ACTIVE' ? 'tag-success' : 'tag-neutral',
          cardCls: i === 0 ? 'bal-main' : 'bal-alt',
          daysLeft: left,
        });
      });
      const soon = decorated.filter((e) => e.status === 'ACTIVE')
        .sort((a, b) => a.daysLeft - b.daysLeft)[0];
      const expiring = (soon && soon.daysLeft <= 30)
        ? { name: soon.product_name, days: soon.daysLeft,
            left: soon.remainText + (soon.ptype === 'STORED' ? ' 元' : ' 节') }
        : null;
      const flows = (ledger || []).map((l) => Object.assign({}, l, {
        up: (l.delta_sessions || 0) > 0,
        deltaText: ((l.delta_sessions || 0) > 0 ? '+' : '') + (l.delta_sessions || 0) + ' 节',
        title: l.remark || '权益变动',
        meta: l.created_at + (l.operator_name ? ' · ' + l.operator_name : ' 登记'),
      }));
      this.setData({
        member: me, remain: me.remain_sessions || 0,
        ents: decorated, ledger: flows, expiring, loading: false,
      });
    } catch (e) {
      this.setData({ loading: false });
      wx.showToast({ title: e.message, icon: 'none' });
    }
  },

  goBooking() { wx.switchTab({ url: '/pages/index/index' }); },
  goMyBookings() { wx.switchTab({ url: '/pages/mybookings/mybookings' }); },
  renewTip() { wx.showToast({ title: '请到前台办理续卡，本系统无线上支付', icon: 'none' }); },
});
