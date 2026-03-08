#!/usr/bin/env bash
#
# LLM API Factory Agent 安装脚本
#
# 使用方法:
#   curl -fsSL https://raw.githubusercontent.com/your-repo/main/scripts/agent_install.sh | bash -s -- \
#     --ws-url ws://localhost:8000/agent/ws \
#     --heartbeat-url http://localhost:8000/agent/heartbeat \
#     --agent-name "edge-hk" \
#     --agent-token "your-token"
#
set -euo pipefail

# 默认配置
WS_URL=""
HEARTBEAT_URL=""
AGENT_NAME=""
AGENT_TOKEN=""
AGENT_REGION=""
AGENT_ENDPOINT_URL=""
AGENT_SCRIPT_URL=""  # 未来用于从远程下载 agent 脚本

# 解析参数
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ws-url)
      WS_URL="$2"
      shift 2
      ;;
    --heartbeat-url)
      HEARTBEAT_URL="$2"
      shift 2
      ;;
    --agent-name)
      AGENT_NAME="$2"
      shift 2
      ;;
    --agent-token)
      AGENT_TOKEN="$2"
      shift 2
      ;;
    --agent-region)
      AGENT_REGION="$2"
      shift 2
      ;;
    --agent-endpoint-url)
      AGENT_ENDPOINT_URL="$2"
      shift 2
      ;;
    --agent-script-url)
      AGENT_SCRIPT_URL="$2"
      shift 2
      ;;
    *)
      echo "未知参数: $1"
      exit 1
      ;;
  esac
done

# 校验必需参数
if [[ -z "$WS_URL" || -z "$HEARTBEAT_URL" || -z "$AGENT_NAME" || -z "$AGENT_TOKEN" ]]; then
  echo "错误: 缺少必需参数"
  echo "用法: $0 --ws-url <url> --heartbeat-url <url> --agent-name <name> --agent-token <token>"
  echo ""
  echo "可选参数:"
  echo "  --agent-region <region>       区域标识 (如 HK/SG/US)"
  echo "  --agent-endpoint-url <url>   出口公网地址"
  echo "  --agent-script-url <url>     Agent 脚本 URL (未来使用)"
  exit 1
fi

echo "=== LLM API Factory Agent 安装 ==="
echo "节点名称: $AGENT_NAME"
echo "WebSocket: $WS_URL"
echo "心跳地址: $HEARTBEAT_URL"
[[ -n "$AGENT_REGION" ]] && echo "区域: $AGENT_REGION"
echo ""

# 确定 Agent 脚本路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_SCRIPT="$SCRIPT_DIR/llm-api-factory-agent.py"

# 如果指定了远程脚本 URL，尝试下载
if [[ -n "$AGENT_SCRIPT_URL" ]]; then
  echo "从远程下载 Agent 脚本..."
  TEMP_SCRIPT="/tmp/llm-api-factory-agent.py"
  if curl -fsSL "$AGENT_SCRIPT_URL" -o "$TEMP_SCRIPT"; then
    AGENT_SCRIPT="$TEMP_SCRIPT"
    echo "下载完成"
  else
    echo "警告: 下载失败，使用本地脚本"
  fi
fi

# 检查 Python 环境 (尝试多个可能的位置，优先 anaconda)
PYTHON_CMD=""
for cmd in /opt/anaconda3/bin/python python3 python ~/.local/bin/python; do
  if command -v "$cmd" >/dev/null 2>&1; then
    # 检查是否包含所需模块
    if $cmd -c "import httpx; import websockets" 2>/dev/null; then
      PYTHON_CMD="$cmd"
      break
    fi
  fi
done

if [[ -z "$PYTHON_CMD" ]]; then
  echo "错误: 未找到包含 httpx 和 websockets 的 Python"
  echo "请先安装: pip install httpx websockets"
  exit 1
fi

echo "使用 Python: $PYTHON_CMD"

# 检查依赖 (已在上一步验证，这里不再重复安装)
echo "依赖检查通过"

# 检查 Agent 脚本
if [[ ! -f "$AGENT_SCRIPT" ]]; then
  echo "错误: 未找到 Agent 脚本: $AGENT_SCRIPT"
  echo ""
  echo "请确保在项目根目录运行此脚本，或通过 --agent-script-url 指定远程脚本"
  exit 1
fi

# 创建日志目录
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

# 构建命令
CMD="$PYTHON_CMD $AGENT_SCRIPT"
CMD="$CMD --ws-url $WS_URL"
CMD="$CMD --heartbeat-url $HEARTBEAT_URL"
CMD="$CMD --name $AGENT_NAME"
CMD="$CMD --token $AGENT_TOKEN"

if [[ -n "$AGENT_REGION" ]]; then
  CMD="$CMD --region $AGENT_REGION"
fi

if [[ -n "$AGENT_ENDPOINT_URL" ]]; then
  CMD="$CMD --endpoint-url $AGENT_ENDPOINT_URL"
fi

# 检查是否已有运行中的 Agent
PID_FILE="$LOG_DIR/agent-$AGENT_NAME.pid"
if [[ -f "$PID_FILE" ]]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "警告: Agent '$AGENT_NAME' 已在运行 (PID: $OLD_PID)"
    read -p "是否要重启? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      echo "取消安装"
      exit 0
    fi
    kill "$OLD_PID" || true
    sleep 2
  fi
fi

# 启动 Agent
echo "启动 Agent..."
nohup $CMD > "$LOG_DIR/agent-$AGENT_NAME.log" 2>&1 &
AGENT_PID=$!
echo "$AGENT_PID" > "$PID_FILE"

echo ""
echo "=== 安装完成 ==="
echo "PID: $AGENT_PID"
echo "日志: $LOG_DIR/agent-$AGENT_NAME.log"
echo ""
echo "查看日志: tail -f $LOG_DIR/agent-$AGENT_NAME.log"
echo "停止 Agent: kill $AGENT_PID"

# 等待一下确认启动成功
sleep 3
if kill -0 "$AGENT_PID" 2>/dev/null; then
  echo ""
  echo "Agent 已启动!"
else
  echo ""
  echo "错误: Agent 启动失败，请查看日志"
  exit 1
fi
