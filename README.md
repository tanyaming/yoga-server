# 场馆连锁客户管理平台

面向健身房 / 瑜伽馆 / 普拉提馆的**多店连锁客户管理平台**：会员端小程序 + 教练工作台 + 商家管理后台。已按《场馆连锁客户管理平台-PRD-v1.0》与「冷底 × 原色」定稿设计系统实现。

## 技术栈

- **后端**：Python 3.11 · FastAPI · SQLAlchemy 2.0 · Alembic（SQLite 开发 / MySQL 生产）
- **管理后台**：原生 HTML/CSS/JS SPA（复用定稿 Design Token）
- **小程序**：微信小程序原生（会员端 + 教练端）

## 目录结构

```
yoga-server/
├── app/                    # 后端（分层：routers → services → repositories → models）
│   ├── main.py             # 应用入口：中间件 / 全局异常 / 健康检查 / 静态挂载
│   ├── config.py           # 集中配置（环境变量 + 启动校验快速失败）
│   ├── errors.py           # 类型化错误体系
│   ├── security.py         # PBKDF2 密码 / JWT / 手机号哈希脱敏
│   ├── dependencies.py     # 认证 + RBAC + 会员认证
│   ├── models/             # ORM 领域模型（全表 tenant_id 贯通）
│   ├── repositories/       # Repository 基类（租户收口）+ 审计
│   ├── services/           # 业务服务（收款/约课/点名/看板/账实核对）
│   ├── routers/            # 控制器（59 条路由）
│   └── seed.py             # 开发种子数据（幂等）
├── migrations/             # Alembic 迁移（3 个版本）
├── web/                    # 管理后台 SPA（/admin 路径同源部署）
├── miniprogram/            # 微信小程序源码
├── requirements.txt
└── .env.example
```

## 快速启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置（复制 .env.example 为 .env，生产必须替换 JWT_SECRET 等）
export JWT_SECRET='dev-secret-0123456789abcdef'

# 3. 启动（dev 模式自动建表 + 种子数据）
python run.py
# 或：python -m uvicorn app.main:app --host 127.0.0.1 --port 8002

# 4. 访问
#    API 文档： http://127.0.0.1:8002/docs
#    管理后台： http://127.0.0.1:8002/admin/
```

**默认账号**（种子数据）：馆主 `admin / admin123`，教练 `coach / coach123`。

## 小程序

用微信开发者工具打开 `miniprogram/`，修改 `miniprogram/utils/config.js` 中的 `BASE_URL` 为后端地址（上线需 HTTPS 合法域名）。会员端：微信登录 → 绑定手机号 → 约课 / 我的权益；教练端：账号密码登录 → 点名消课。

## 核心设计

- **多租户**：租户=品牌 → 门店 → 角色；全表 `tenant_id` 贯通，Repository 层强制租户隔离（越权零容忍 + 审计）。
- **无在线支付口径**：线下收款线上记账，金额以「分」存储；收款幂等（幂等键 + 参数一致性校验）。
- **账实一致**：权益账本（Entitlement + LedgerEntry 只追加）+ 消课幂等（member+session 唯一约束）+ 日终台账核对差异挂起。
- **约课五态**：BOOKED / WAITLIST / CANCELLED / ATTENDED / NO_SHOW，候补递补、爽约限约（3 次 / 7 天可配）。
- **安全**：JWT 短时效访问令牌 + 服务端刷新令牌哈希；手机号加盐哈希；结构化 JSON 日志脱敏。

## 生产部署要点

- `DATABASE_URL` 切 MySQL（`mysql+pymysql://...`），迁移用 `alembic upgrade head`。
- 关闭 `WX_MOCK`、配置 `WX_APPID/WX_SECRET`、替换 `JWT_SECRET` / `BOOTSTRAP_ADMIN_PASSWORD`。
- CORS 显式来源，禁止通配符；`APP_ENV=prod` 时启动校验会强制拦截不安全配置。
