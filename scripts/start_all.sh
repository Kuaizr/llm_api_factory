#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/scripts/logs"
PID_DIR="$ROOT_DIR/scripts/pids"

PORT=8000
HOST="0.0.0.0"
PYTHON_BIN="/opt/anaconda3/bin/python"
API_BASE=""
ADMIN_TOKEN="${LLM_MASTER_AUTH_TOKEN:-admin}"
DISABLE_AUTH=0
REBUILD_FRONTEND=0
SKIP_BUILD=0
FOREGROUND=0
LEGACY_FRONTEND_PORT=""

usage() {
  cat <<'EOF'
单入口一键启动（后端托管前端 dist）

用法:
  bash scripts/start_all.sh [options]

选项:
  --port <port>             单入口端口，默认 8000
  --backend-port <port>     兼容旧参数，等同于 --port
  --frontend-port <port>    兼容旧参数，单入口模式下会忽略
  --host <host>             监听地址，默认 0.0.0.0
  --api-base <url>          前端构建时注入的 VITE_API_BASE
  --admin-token <token>     管理员登录密码（默认 admin）
  --disable-auth            关闭后台鉴权（不建议生产使用）
  --rebuild-frontend        启动前重新执行前端 build
  --skip-build              跳过前端 build（要求 dist 已存在）
  --foreground              前台运行（默认后台）
  --help                    显示帮助

示例:
  bash scripts/start_all.sh --port 9000
  bash scripts/start_all.sh --port 9000 --foreground
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port|--backend-port)
      PORT="$2"
      shift 2
      ;;
    --frontend-port)
      LEGACY_FRONTEND_PORT="$2"
      shift 2
      ;;
    --host)
      HOST="$2"
      shift 2
      ;;
    --api-base)
      API_BASE="$2"
      shift 2
      ;;
    --admin-token)
      ADMIN_TOKEN="$2"
      shift 2
      ;;
    --disable-auth)
      DISABLE_AUTH=1
      shift
      ;;
    --rebuild-frontend)
      REBUILD_FRONTEND=1
      shift
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --foreground)
      FOREGROUND=1
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ -n "$LEGACY_FRONTEND_PORT" ]]; then
  echo "提示: --frontend-port=$LEGACY_FRONTEND_PORT 在单入口模式下会被忽略。"
fi

if [[ "$SKIP_BUILD" -eq 1 && "$REBUILD_FRONTEND" -eq 1 ]]; then
  echo "参数冲突: --skip-build 与 --rebuild-frontend 不能同时使用"
  exit 1
fi

if [[ "$DISABLE_AUTH" -eq 0 && -z "$ADMIN_TOKEN" ]]; then
  echo "参数错误: 开启鉴权时 admin token 不能为空"
  exit 1
fi

mkdir -p "$LOG_DIR" "$PID_DIR"

APP_LOG="$LOG_DIR/app-${PORT}.log"
APP_PID_FILE="$PID_DIR/app-${PORT}.pid"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "未找到固定 Python: $PYTHON_BIN"
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "未找到 npm，请先安装 Node.js"
  exit 1
fi

stop_if_running() {
  local pid_file="$1"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "停止已有进程 PID=$pid"
      kill "$pid" || true
      sleep 1
    fi
    rm -f "$pid_file"
  fi
}

if [[ ! -d "$FRONTEND_DIR/dist" ]]; then
  if [[ "$SKIP_BUILD" -eq 1 ]]; then
    echo "前端 dist 不存在，无法 --skip-build"
    exit 1
  fi
  REBUILD_FRONTEND=1
fi

if [[ "$SKIP_BUILD" -eq 0 && "$REBUILD_FRONTEND" -eq 1 ]]; then
  if [[ -n "$API_BASE" ]]; then
    echo "执行前端构建: VITE_API_BASE=${API_BASE}"
    (
      cd "$FRONTEND_DIR"
      VITE_API_BASE="$API_BASE" npm run build
    )
  else
    echo "执行前端构建: 使用同源 API（window.location.origin）"
    (
      cd "$FRONTEND_DIR"
      npm run build
    )
  fi
fi

stop_if_running "$APP_PID_FILE"

if lsof -n -P -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 $PORT 已被其他进程占用，请先释放后重试"
  exit 1
fi

echo "启动单入口服务: ${HOST}:${PORT}"
if [[ "$FOREGROUND" -eq 1 ]]; then
  (
    cd "$BACKEND_DIR"
    if [[ "$DISABLE_AUTH" -eq 0 ]]; then
      export LLM_MASTER_AUTH_TOKEN="$ADMIN_TOKEN"
    else
      unset LLM_MASTER_AUTH_TOKEN
    fi
    exec "$PYTHON_BIN" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
  ) >"$APP_LOG" 2>&1 &
else
  if [[ "$DISABLE_AUTH" -eq 0 ]]; then
    LLM_MASTER_AUTH_TOKEN="$ADMIN_TOKEN" nohup bash -lc "cd '$BACKEND_DIR' && exec '$PYTHON_BIN' -m uvicorn app.main:app --host '$HOST' --port '$PORT'" >"$APP_LOG" 2>&1 &
  else
    nohup bash -lc "cd '$BACKEND_DIR' && exec '$PYTHON_BIN' -m uvicorn app.main:app --host '$HOST' --port '$PORT'" >"$APP_LOG" 2>&1 &
  fi
fi
APP_PID=$!
echo "$APP_PID" >"$APP_PID_FILE"

sleep 2
if ! kill -0 "$APP_PID" 2>/dev/null; then
  echo "启动失败，请查看日志: $APP_LOG"
  exit 1
fi

echo ""
echo "启动成功（单入口）"
echo "访问地址: http://127.0.0.1:${PORT}"
echo "API 示例: http://127.0.0.1:${PORT}/openai/v1/models"
if [[ "$DISABLE_AUTH" -eq 0 ]]; then
  echo "管理员密码: ${ADMIN_TOKEN}"
else
  echo "管理员鉴权: 已关闭"
fi
echo "日志文件: $APP_LOG"
echo "停止命令: kill $(cat "$APP_PID_FILE")"

if [[ "$FOREGROUND" -eq 1 ]]; then
  cleanup() {
    echo "收到退出信号，停止进程..."
    kill "$APP_PID" 2>/dev/null || true
  }
  trap cleanup INT TERM
  wait "$APP_PID"
fi
