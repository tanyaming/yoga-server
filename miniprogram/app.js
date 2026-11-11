// 小程序入口
const api = require('./utils/api');

App({
  globalData: {
    member: null,
    memberLoggedIn: false,
  },
  onLaunch() {
    // 尝试恢复会员登录态
    const token = api.getToken('member');
    this.globalData.memberLoggedIn = !!token;
  },
});
