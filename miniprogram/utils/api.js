// 小程序 API 客户端：token 存 storage，401 自动清除并触发重登录
const config = require('./config');

const TOKEN_KEYS = { member: 'member_token', coach: 'coach_token' };

function getToken(kind) {
  return wx.getStorageSync(TOKEN_KEYS[kind] || 'member_token') || '';
}
function setToken(kind, v) {
  const k = TOKEN_KEYS[kind] || 'member_token';
  v ? wx.setStorageSync(k, v) : wx.removeStorageSync(k);
}

function request(kind, path, { method = 'GET', data } = {}) {
  return new Promise((resolve, reject) => {
    const token = getToken(kind);
    wx.request({
      url: config.BASE_URL + path,
      method,
      data,
      header: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: 'Bearer ' + token } : {}),
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          const msg = (res.data && res.data.error && res.data.error.message) || ('请求失败 ' + res.statusCode);
          if (res.statusCode === 401) setToken(kind, '');
          reject(new Error(msg));
        }
      },
      fail() { reject(new Error('网络连接失败')); },
    });
  });
}

// 会员端登录：code → openid → 是否需绑定
function memberLogin() {
  return new Promise((resolve, reject) => {
    wx.login({
      success: ({ code }) => {
        request('member', '/api/auth/wx-login', {
          method: 'POST', data: { code, tenant_id: config.DEFAULT_TENANT_ID },
        }).then(resolve).catch(reject);
      },
      fail: () => reject(new Error('微信登录失败')),
    });
  });
}

// 会员绑定手机号
function memberBind(phone, name) {
  return new Promise((resolve, reject) => {
    wx.login({
      success: ({ code }) => {
        request('member', '/api/auth/wx-bind', {
          method: 'POST',
          data: { code, tenant_id: config.DEFAULT_TENANT_ID, phone, name },
        }).then((d) => { setToken('member', d.token); resolve(d); }).catch(reject);
      },
      fail: () => reject(new Error('微信登录失败')),
    });
  });
}

module.exports = { request, getToken, setToken, memberLogin, memberBind };
