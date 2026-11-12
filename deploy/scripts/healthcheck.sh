#!/usr/bin/env bash
# 健康巡检（T-34）：/health、/ready、/admin/、容器状态、磁盘、证书到期
# 任一项失败即非零退出，可直接接 cron + 告警。
# crontab 示例：*/5 * * * * /opt/yoga-server/deploy/scripts/healthcheck.sh
set -uo pipefail

BASE="${BASE_URL:-http://127.0.0.1:8002}"
DOMAIN="${DOMAIN:-}"
COMPOSE_FILE="${COMPOSE_FILE:-$(cd "$(dirname "$0")/.." && pwd)/docker-compose.yml}"
FAIL=0

check() {
  local name="$1" url="$2" expect="${3:-200}" code
  code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$url" 2>/dev/null || echo 000)
  if [ "$code" = "$expect" ]; then
    echo "OK   $name ($code)"
  else
    echo "FAIL $name (期望 $expect，实际 $code)"
    FAIL=1
  fi
}

check "健康检查 /health" "$BASE/health"
check "就绪检查 /ready"  "$BASE/ready"
check "管理后台 /admin/" "$BASE/admin/"

# systemd 部署时检查服务状态（容器部署则跳过）
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^yoga-server.service'; then
  if systemctl is-active --quiet yoga-server; then
    echo "OK   systemd yoga-server active"
  else
    echo "FAIL systemd yoga-server 未运行"
    FAIL=1
  fi
fi

# 容器部署时检查容器健康
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  BAD=$(docker compose -f "$COMPOSE_FILE" ps --format '{{.Service}} {{.State}}' 2>/dev/null | grep -v " running" || true)
  if [ -n "$BAD" ]; then
    echo "FAIL 容器状态异常：$BAD"
    FAIL=1
  else
    echo "OK   容器均 running"
  fi
fi

# 磁盘使用率 >= 85% 视为异常
USE=$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')
if [ "${USE:-0}" -ge 85 ]; then
  echo "FAIL 根分区使用率 ${USE}%"
  FAIL=1
else
  echo "OK   根分区使用率 ${USE}%"
fi

# 证书到期（仅当配置 DOMAIN 时检查）
if [ -n "$DOMAIN" ]; then
  END=$(echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN:443" 2>/dev/null \
        | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
  if [ -n "$END" ]; then
    END_TS=$(date -d "$END" +%s 2>/dev/null || echo 0)
    DAYS=$(( (END_TS - $(date +%s)) / 86400 ))
    if [ "$DAYS" -lt 14 ]; then
      echo "FAIL 证书 ${DAYS} 天后到期"
      FAIL=1
    else
      echo "OK   证书剩余 ${DAYS} 天"
    fi
  else
    echo "WARN 无法读取证书到期时间"
  fi
fi

exit "$FAIL"
