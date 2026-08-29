# 晨蕴瑜伽约课后端（FastAPI 重构版）

原项目后端为 Rust (Rocket) + PostgreSQL，且业务逻辑大量写在 PostgreSQL 数据库函数中。本目录是**使用 Python 3 + FastAPI + PyMySQL 的完整重写版**，直接对接已迁移到远程 MySQL (`yuekeDB`) 的表结构。

---

## 1. 已实现的接口（与原 Rust 后端完全对齐）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/yoga/auth?code=` | 微信 code2session 登录代理 |
| GET | `/yoga/index` | 首页聚合（轮播、最近预约、金刚区、教练、营销、公告） |
| GET | `/yoga/lessons?start&openid&class_type` | 某日课程列表 |
| GET | `/yoga/book?id&openid` | 预约课程，返回预约 id |
| GET | `/yoga/unbook?id&openid` | 取消预约，返回被删 id |
| GET | `/yoga/user/query?openid` | 用户资料查询 |
| POST | `/yoga/user` | 用户注册/更新 |
| GET | `/yoga/user/book/statistics?id` | 约课统计（团/小/私） |
| GET | `/yoga/teacher/lessons?start_time&end_time&open_id&class_type&teacher_id` | 教练主页 |
| GET | `/yoga/admin/lessons?start&end` | 管理端课程列表 |
| GET | `/yoga/admin/lesson?id` | 单节课详情 |
| GET | `/yoga/admin/lesson/hidden?id&status` | 停课/恢复 |
| GET | `/yoga/admin/lesson/delete?id` | 删除课程（连带清理预约） |
| GET | `/yoga/admin/lessons/and/teachers?id` | 排课编辑页下拉数据 |
| POST | `/yoga/lesson/update` | 按周系列批量修改课程 |
| POST | `/yoga/admin/lessons/update?open_id` | 按星期批量排课（一年） |
| GET | `/yoga/admin/user/lessons?id&start&end&open_id` | 某会员约课记录 |
| GET | `/yoga/admin/users/all?open_id` | 会员列表（仅 admin user_type=4） |
| GET | `/yoga/admin/user?open_id&id` | 会员详情 |
| GET | `/yoga/admin/schedule` | 生成本周课表 PNG 图片 |
| POST | `/yoga/picture` | 图片上传 |
| POST | `/yoga/avatar` | 头像上传（保存为 `.avif`） |
| POST | `/yoga/debug` | 客户端系统信息上报 |
| GET | `/favicon.ico` | 404 |

新增：
- `GET /ping` 健康检查
- `GET /admin` 管理后台静态 SPA（概览 / 课程 / 会员 / 周课表 / 基础数据）

---

## 2. 快速启动

```bash
cd yoga-server
# 0. 复制配置模板并填入真实数据库/小程序凭证
cp .env.example .env

# 1. 创建虚拟环境（首次）
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 启动（默认监听 127.0.0.1:8002，与原 Rust 后端端口一致）
.venv/bin/python run.py

# 或直接用 uvicorn
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8002
```

### 配置（.env 或环境变量）

配置优先读项目根目录 `.env`（不进 git 仓库，参考 `.env.example`），其次读环境变量：

| 变量 | 说明 |
|------|------|
| `DB_HOST` | MySQL 主机 |
| `DB_PORT` | 端口（默认 3306） |
| `DB_USER` | 用户名 |
| `DB_PASSWORD` | 密码 |
| `DB_NAME` | 数据库名（默认 `yuekeDB`） |
| `APPID` | 微信小程序 AppID |
| `SECRET` | 微信小程序 Secret |
| `IMAGE_DIR` | 上传文件目录（默认 `./images`） |
| `HOST`/`PORT` | 服务监听地址（默认 `127.0.0.1`/`8002`） |

**注意**：AppID 上线前必须替换为自己的小程序凭证。

---

## 3. 自测

已提供两套完整自测脚本，分别验证用户端与管理端，数据库连接真实远程 `yuekeDB`：

```bash
.venv/bin/python tests/smoke_user.py   # 用户端：19 项断言
.venv/bin/python tests/smoke_admin.py  # 管理端：19 项断言
```

也可以本地 curl：

```bash
curl http://127.0.0.1:8002/ping
curl http://127.0.0.1:8002/yoga/index
curl http://127.0.0.1:8002/yoga/admin/schedule -o schedule.png
```

## 3.1 管理后台 Web 前端

服务启动后，浏览器打开：

```
http://127.0.0.1:8002/admin
```

功能：
- 概览看板：本周课程数、预约人次、会员/教练/课程/公告统计
- 课程管理：周视图课程列表、停课/恢复、删除、批量排课、系列修改
- 会员管理：会员列表、详情抽屉（资料/约课统计/约课记录）
- 周课表：自动生成 PNG 预览并支持下载
- 基础数据：课程库与教练列表

登录方式：输入管理员 OpenID 校验 `user_type=4`；本地已灌演示数据，可直接点「使用演示管理员登录」。

如需重新生成/重置演示数据：

```bash
.venv/bin/python tests/seed_demo.py
```

---

## 4. 小程序端调试

本仓库已包含完整小程序源码（来自源项目，`miniprogram/` 目录），微信开发者工具**直接打开仓库根目录**即可（`project.config.json` 在根目录，`miniprogramRoot` 指向 `miniprogram/`）。

本地调试步骤：

1. 先启动后端：`.venv/bin/python run.py`（监听 127.0.0.1:8002）
2. 微信开发者工具导入本仓库根目录
3. `project.config.json` 已设 `urlCheck: false`（不校验合法域名），`miniprogram/app.js` 中 `host`/`staticHost` 已指向 `http://127.0.0.1:8002`，可直接联调
4. 真机预览需在「详情 → 本地设置」勾选「不校验合法域名…」

注意事项：

- `project.config.json` 里的 `appid` 是原项目作者的（`wx915afa9083177059`），**登录获取 openid 需要换成自己的小程序 AppID**（并在 `.env` 中配置对应 `APPID`/`SECRET`）。开发调试页面 UI 可先用测试号
- 轮播图等图片资源存放在后端 `images/` 目录（`GET /images/` 静态服务），演示库引用的 `s1.jpg` 等需要自行上传
- 原项目小班/私教/公告等页面仍调旧版 `v1/*` 接口，当前后端未实现这些路由，页面会显示空数据；核心链路（首页/团课约课/取消/管理端）已全部打通

## 4.1 小程序端上线对接

修改 `miniprogram/app.js` 中的 `host` 与 `staticHost`：

```js
host: 'https://你的域名',
staticHost: 'https://你的域名',
```

后端默认监听 `127.0.0.1:8002`，生产环境需要：

1. 公网服务器上运行本服务（或 Docker、systemd 等守护）
2. Nginx 反向代理 + HTTPS 证书（小程序强制 HTTPS）
3. 在微信公众平台配置 `request 合法域名` 和 `uploadFile 合法域名`
4. 把 `APPID` / `SECRET` 环境变量设为自己的小程序凭证

---

## 5. 与原后端的差异

| 项目 | 原 Rust | FastAPI 版 |
|------|---------|-------------|
| 语言/框架 | Rust + Rocket | Python 3.13 + FastAPI |
| 数据库 | PostgreSQL | MySQL 8.0 |
| 业务逻辑 | 大量写在 `fn_*` 存储函数中 | 全部写在 Python 服务层（便于调试/扩展） |
| 课表图片 | Rust imageproc + rusttype | Pillow |
| 登录 | 直接代理 code2session | 直接代理 code2session（一致） |

接口路径、入参、返回 JSON 字段与原后端保持一致，可直接替换 `host` 后使用。

---

## 6. 目录结构

```
yoga-server/
├── app/
│   ├── config.py           # .env/环境变量配置加载
│   ├── db.py               # PyMySQL + PooledDB 连接池
│   ├── main.py             # FastAPI 入口
│   ├── responses.py        # 兼容 Rocket 的响应格式
│   ├── timeutil.py         # 北京时间/UTC 时间戳工具
│   ├── wechat.py           # 微信登录代理
│   ├── routers/            # 路由层
│   └── services/           # 业务层（对应原 fn_*）
├── tests/
│   ├── smoke_user.py       # 用户端自测
│   ├── smoke_admin.py      # 管理端自测
│   └── seed_demo.py        # 演示数据
├── web/                    # 管理后台 SPA
│   ├── index.html
│   ├── admin.css
│   └── admin.js
├── miniprogram/            # 微信小程序源码（来自源项目）
│   ├── app.js / app.json
│   ├── pages/              # 19 个页面
│   ├── components / custom-tab-bar
│   ├── pkg/                # Rust 编译的 WASM 前端逻辑（源项目自带）
│   └── images / wemark / utils
├── project.config.json     # 微信开发者工具项目配置（打开仓库根目录即导入）
├── docs/                   # 需求文档 / 部署指南 / MySQL 迁移脚本
├── run.py                  # 启动脚本
├── requirements.txt
├── PingFang.ttf            # 课表图片字体
└── pattern.png             # 课表图片模板
```
