# 部署手册

> 本文件记录 **2026-11-12 实际执行的部署路径**，不是理论方案。两套方案并存：A 为当前线上方式（systemd），B 为容器化备选（本机验证用，服务器未装 Docker）。

## 一、当前线上环境

| 项 | 值 |
|---|---|
| 服务器 | 腾讯云轻量 `124.220.52.50`（Ubuntu 26.04，4C/4G/40G） |
| 登录 | `ubuntu@124.220.52.50:22`（sudo 免密） |
| 代码目录 | `/opt/yoga-server`（**非 git 仓库**，由 rsync 同步） |
| 虚拟环境 | `/opt/yoga-server/venv`（Python 3.14.4） |
| 进程管理 | systemd `yoga-server.service` → uvicorn `127.0.0.1:8002` |
| 反向代理 | nginx 1.28.3，`/etc/nginx/sites-enabled/yoga-server`，80 → 8002 |
| 数据库 | MySQL 8.4.11，库 `yoga_db`，用户 `yoga_app` |
| 配置 | `/opt/yoga-server/.env`（`APP_ENV=staging`、`SEED_DEMO=true`、`WX_MOCK=false`） |
| 备份 | `/opt/backups/`（数据库 dump + 代码 tar） |

访问：`http://124.220.52.50/`（API）、`http://124.220.52.50/admin/`（后台）、`/docs`（OpenAPI）

## 二、A 方案：systemd 直部署（当前线上）

### 首次部署 / 日常更新

```bash
# 0. 本地确认测试全绿
.venv/bin/python -m pytest -q          # 期望 181 passed

# 1. 服务器备份（务必先做）
ssh ubuntu@124.220.52.50
STAMP=$(date +%Y%m%d-%H%M%S)
sudo mkdir -p /opt/backups && sudo chown ubuntu:ubuntu /opt/backups
sudo mysqldump --single-transaction --quick yoga_db | gzip -9 > /opt/backups/yoga_db-$STAMP.sql.gz
tar czf /opt/backups/yoga-code-$STAMP.tar.gz -C /opt \
  --exclude='yoga-server/venv' --exclude='yoga-server/.env' --exclude='__pycache__' yoga-server

# 2. 本地同步代码（排除环境与本地数据）
rsync -az --exclude='.venv/' --exclude='venv/' --exclude='.env' \
  --exclude='__pycache__/' --exclude='.git/' --exclude='.workbuddy/' \
  --exclude='*.db' --exclude='.pytest_cache/' --exclude='.coverage' \
  ./ ubuntu@124.220.52.50:/opt/yoga-server/

# 3. 依赖（仅在 requirements.lock 变化时执行）
cd /opt/yoga-server && venv/bin/pip install -r requirements.lock

# 4. 迁移前先看超员影响（只读）
venv/bin/python scripts/repair_booked.py --dry-run

# 5. 迁移 + 重启
venv/bin/alembic upgrade head
sudo systemctl restart yoga-server
sleep 5 && systemctl is-active yoga-server

# 6. 验证
curl -fsS http://127.0.0.1:8002/health
curl -fsS http://127.0.0.1:8002/ready
curl -sI http://127.0.0.1:8002/admin/ | head -1
```

### 回滚（3 分钟内）

```bash
cd /opt
STAMP=<出问题前的备份时间戳>
sudo systemctl stop yoga-server
tar xzf /opt/backups/yoga-code-$STAMP.tar.gz -C /opt          # 覆盖代码，venv/.env 不受影响
sudo mysql yoga_db < <(gunzip -c /opt/backups/yoga_db-$STAMP.sql.gz)
sudo systemctl start yoga-server
curl -fsS http://127.0.0.1:8002/health
```

> 迁移回退也可用 `alembic downgrade -1`（两条新迁移均实测可回退），但**数据修复不可逆**（超员 BOOKED 降级为 WAITLIST），所以优先用整库恢复。

### 当前配置待办

- `APP_ENV=staging` + `SEED_DEMO=true`：每次重启会补种 3 节演示课。**接真实客户前必须把 `SEED_DEMO` 改为 `false`**，并评估是否切 `APP_ENV=prod`（切 prod 会强制校验 JWT_SECRET / CORS / WX 配置）。
- 无 HTTPS：微信小程序真机要求 HTTPS + 备案域名，当前仅 HTTP + IP，只能开发者工具内测（关闭域名校验）。

## 三、B 方案：Docker Compose（备选）

服务器未安装 Docker，本方案用于本机或有 Docker 的环境：

```bash
cp deploy/.env.prod.example deploy/.env && vi deploy/.env
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
docker compose -f deploy/docker-compose.yml logs -f app
```

包含：`mysql:8.0.36`（≥8.0.16 才强制 CHECK 约束）、app 容器（`deploy/entrypoint.sh` 等库就绪 → `alembic upgrade head` → uvicorn 双 worker）、数据库端口不对外暴露。

## 四、备份与巡检

```bash
# 每日 02:00 备份（服务器 crontab）
0 2 * * * cd /opt/yoga-server && BACKUP_DIR=/opt/backups ./deploy/scripts/backup.sh >> /var/log/yoga-backup.log 2>&1

# 每 5 分钟巡检，失败即非零退出（可接告警）
*/5 * * * * /opt/yoga-server/deploy/scripts/healthcheck.sh || echo "yoga-server 巡检失败" | mail -s ALERT you@example.com
```

`backup.sh` 产出 `yoga-YYYYmmdd-HHMMSS.sql.gz` + `checksums.txt`，保留 7 天；`restore.sh --confirm <file>` 要求二次确认并校验迁移版本与表数。

## 五、2026-11-12 部署记录

- 迁移：`6ecded13fd8f` → `7c1e9a2b3d4f` → `a1b7c3d5e901` → `b2c8d4e6f012`（3 条，含超员修复与 CHECK）
- 超员 dry-run：无需修复（0 条）
- 表数：21 → 23（补齐 `coach_reviews`、`schedule_change_requests`）
- 生产 MySQL 验收（临时数据，跑完已删）：
  - 并发 5 人抢 1 名额：BOOKED=1 / WAITLIST=4 / booked=1 ✅
  - 退款回冲：收款 +199900 → 退款后回到原值 ✅
  - CHECK 约束拦截 `booked=5 > capacity=1` ✅
- 首次部署时发现并修复 2 个**仅在 MySQL 出现**的缺陷（已回滚到代码并重新部署）：
  1. `REPEATABLE READ` 快照导致满员误判，并发会员收到 409 而非进候补 → 改为只信 `rowcount`
  2. `UPDATE` 同表子查询（错误 1093）→ 取号改为派生表包裹
- 备份：`/opt/backups/yoga_db-20261112-184758.sql.gz`、`yoga-code-20261112-184806.tar.gz`
