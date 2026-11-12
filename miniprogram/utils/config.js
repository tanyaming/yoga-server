/**
 * 小程序后端地址与环境切换
 * ------------------------------------------------------------------
 * 真机联调前只需改下面两处：
 *   1) 把 REAL_URL 换成你的 HTTPS 域名
 *   2) 把 ENV 改成 'real'
 *
 * ENV = 'devtool' 开发者工具模拟器 / 真机调试
 *       配合「详情 → 本地设置 → 不校验合法域名」可直连 HTTP + IP。
 *       注意：该开关是否在真机调试生效随工具版本变化，需以本地工具实测为准。
 * ENV = 'local'   本机自行启动后端（127.0.0.1:8002）
 * ENV = 'real'    真机预览 / 体验版 / 正式版
 *       必须 HTTPS + 已备案域名，且已在微信公众平台配置为 request 合法域名；
 *       不支持 IP、不支持端口号、不支持自签证书（以微信官方最新规则为准）。
 */
const ENV = 'local';

// ↓↓↓ 真机联调 / 正式发布前：把 REAL_URL 换成你的 HTTPS 域名（不带端口、不带路径、不带结尾斜杠）
// 例：'https://api.suxu.com'
const REAL_URL = 'https://YOUR_HTTPS_DOMAIN';

const URLS = {
  devtool: 'http://127.0.0.1:8002',  // 开发者工具模拟器：连本机后端
  local: 'http://127.0.0.1:8002',    // 本机自起后端
  real: REAL_URL,
};

// 发布前置硬校验（BUG-001）：切到 real 环境但域名仍是占位符时，fail-fast 阻止误发布
if (ENV === 'real' && REAL_URL === 'https://YOUR_HTTPS_DOMAIN') {
  throw new Error('正式环境域名未配置：请将 utils/config.js 的 REAL_URL 替换为已备案 HTTPS 域名，'
    + '并在微信公众平台配置为 request 合法域名');
}

module.exports = {
  BASE_URL: URLS[ENV] || URLS.local,
  DEFAULT_TENANT_ID: 'tenant-suxu',
  ENV,
  // 教练工作台入口开关。会员端默认降级为「我的」页底部的小字链接，
  // 避免把教练能力放在会员信息旁的显眼位置（产品角色混淆）。
  // 联调 / 演示期保持 true；上线前改为 false 即完全隐藏，
  // 教练端届时改由独立入口或按角色下发进入。
  SHOW_COACH_ENTRY: true,
};
