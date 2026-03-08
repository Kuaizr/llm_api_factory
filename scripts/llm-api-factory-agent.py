#!/usr/bin/env python3
"""
LLM API Factory Agent - 用于跨境代理加速的轻量级客户端

安装依赖:
    pip install httpx websockets

Usage:
    python llm-api-factory-agent.py --ws-url ws://localhost:8000/agent/ws \
        --heartbeat-url http://localhost:8000/agent/heartbeat \
        --name "edge-hk" \
        --token "your-token-here"

Options:
    --ws-url           WebSocket 连接地址 (必需)
    --heartbeat-url    心跳上报地址 (必需)
    --name             节点名称 (必需)
    --token            认证 Token (必需)
    --region           区域标识 (可选, 如 HK/SG/US)
    --endpoint-url     出口公网地址 (可选, 用于延迟探测)
    --heartbeat-interval 心跳间隔秒数 (默认 20)
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import random
import signal
import sys
import time
from typing import Any

# 确保输出立即刷新（对于 nohup/后台运行很重要）
if sys.stdout.isatty() == False:
    sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)
    sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', buffering=1)

import httpx
import websockets


class LLMAgent:
    """LLM API Factory Agent 客户端"""

    def __init__(
        self,
        ws_url: str,
        heartbeat_url: str,
        name: str,
        token: str,
        region: str | None = None,
        endpoint_url: str | None = None,
        heartbeat_interval: int = 20,
    ):
        self.ws_url = ws_url
        self.heartbeat_url = heartbeat_url
        self.name = name
        self.token = token
        self.region = region
        self.endpoint_url = endpoint_url
        self.heartbeat_interval = heartbeat_interval
        self.ws = None
        self.running = True
        self.capabilities: dict[str, Any] = {}

    async def probe_capabilities(self) -> dict[str, Any]:
        """探测支持的模型能力和延迟"""
        capabilities = {
            "supports_gpt": False,
            "supports_gemini": False,
            "supports_claude": False,
            "probe_latency_ms": None,
        }

        # 探测 OpenAI 兼容端点
        test_endpoints = [
            "https://api.openai.com/v1/models",
            "https://api.anthropic.com/v1/models",
            "https://generativelanguage.googleapis.com/v1/models",
        ]

        async with httpx.AsyncClient(timeout=5.0) as client:
            for url in test_endpoints:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        if "openai" in url:
                            capabilities["supports_gpt"] = True
                        elif "anthropic" in url:
                            capabilities["supports_claude"] = True
                        elif "google" in url:
                            capabilities["supports_gemini"] = True
                except Exception:
                    pass

        # 探测到自身的延迟
        if self.endpoint_url:
            try:
                start = time.time()
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.get(self.endpoint_url)
                capabilities["probe_latency_ms"] = int((time.time() - start) * 1000)
            except Exception:
                capabilities["probe_latency_ms"] = random.randint(50, 200)
        else:
            capabilities["probe_latency_ms"] = random.randint(50, 200)

        return capabilities

    async def http_heartbeat(self) -> None:
        """发送 HTTP 心跳"""
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        payload = {
            "name": self.name,
            "token": self.token,
            "region": self.region,
            "endpoint_url": self.endpoint_url,
            **self.capabilities,
        }

        while self.running:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        self.heartbeat_url,
                        json=payload,
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        print(f"[{self.name}] Heartbeat OK")
                    else:
                        print(f"[{self.name}] Heartbeat failed: {resp.status_code}")
            except Exception as e:
                print(f"[{self.name}] Heartbeat error: {e}")

    async def handle_proxy_request(self, payload: dict) -> None:
        """处理代理请求"""
        if not self.ws:
            return

        request_id = payload.get("request_id")
        method = payload.get("method", "POST")
        url = payload.get("url")
        headers = payload.get("headers", {})
        body_b64 = payload.get("body", "")

        # 解码请求体
        try:
            if body_b64:
                body = base64.b64decode(body_b64)
            else:
                body = b""
        except Exception:
            body = body_b64.encode() if isinstance(body_b64, str) else b""

        print(f"[{self.name}] Proxy request: {method} {url}")

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                if method == "GET":
                    resp = await client.request(method, url, headers=headers)
                else:
                    resp = await client.request(method, url, headers=headers, content=body)

                # 准备响应头
                response_headers = dict(resp.headers)
                if "content-type" not in response_headers:
                    response_headers["content-type"] = "application/json"

                # 编码响应体
                resp_body_b64 = base64.b64encode(resp.text.encode()).decode()

                # 发送响应
                await self.ws.send(json.dumps({
                    "type": "proxy_response",
                    "request_id": request_id,
                    "status_code": resp.status_code,
                    "headers": response_headers,
                    "body": resp_body_b64,
                }))

                print(f"[{self.name}] Proxy response: {resp.status_code}")

        except Exception as e:
            print(f"[{self.name}] Proxy error: {e}")
            # 发送错误响应
            try:
                error_body = base64.b64encode(json.dumps({"error": str(e)}).encode()).decode()
                await self.ws.send(json.dumps({
                    "type": "proxy_response",
                    "request_id": request_id,
                    "status_code": 502,
                    "headers": {"content-type": "application/json"},
                    "body": error_body,
                }))
            except Exception:
                pass

    async def connect(self) -> None:
        """连接到 WebSocket 并处理消息"""
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        # 设置额外的心跳消息
        heartbeat_task = None

        while self.running:
            try:
                async with websockets.connect(
                    self.ws_url,
                    additional_headers=headers,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    self.ws = ws
                    print(f"[{self.name}] Connected to {self.ws_url}")

                    # 注册
                    await ws.send(json.dumps({
                        "type": "register",
                        "name": self.name,
                        "token": self.token,
                        "region": self.region,
                        "endpoint_url": self.endpoint_url,
                        **self.capabilities,
                    }))

                    # 启动 HTTP 心跳任务
                    heartbeat_task = asyncio.create_task(self.http_heartbeat())

                    # 处理消息
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            msg_type = data.get("type")

                            if msg_type == "registered":
                                print(f"[{self.name}] Registered successfully")
                            elif msg_type == "heartbeat_ack":
                                pass  # 心跳响应
                            elif msg_type == "proxy_request":
                                await self.handle_proxy_request(data)
                            elif msg_type == "error":
                                print(f"[{self.name}] Server error: {data.get('error')}")
                            else:
                                print(f"[{self.name}] Unknown message type: {msg_type}")

                        except json.JSONDecodeError:
                            print(f"[{self.name}] Invalid JSON: {message[:100]}")
                        except Exception as e:
                            print(f"[{self.name}] Message error: {e}")

            except websockets.exceptions.ConnectionClosed:
                print(f"[{self.name}] Connection closed, reconnecting...")
            except Exception as e:
                print(f"[{self.name}] Connection error: {e}")

            # 等待重连
            if self.running:
                await asyncio.sleep(5)

        # 取消心跳任务
        if heartbeat_task:
            heartbeat_task.cancel()

    async def run(self) -> None:
        """运行 Agent"""
        print(f"[{self.name}] Starting LLM API Factory Agent...")
        print(f"[{self.name}] WS: {self.ws_url}")
        print(f"[{self.name}] Heartbeat: {self.heartbeat_url}")

        # 探测能力
        print(f"[{self.name}] Probing capabilities...")
        self.capabilities = await self.probe_capabilities()
        print(f"[{self.name}] Capabilities: {self.capabilities}")

        # 设置信号处理
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._shutdown)
            except NotImplementedError:
                # Windows 不支持 add_signal_handler
                pass

        # 连接
        await self.connect()

    def _shutdown(self) -> None:
        """关闭 Agent"""
        print(f"[{self.name}] Shutting down...")
        self.running = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LLM API Factory Agent - 跨境代理加速客户端",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--ws-url",
        required=True,
        help="WebSocket 连接地址 (必需)",
    )
    parser.add_argument(
        "--heartbeat-url",
        required=True,
        help="心跳上报地址 (必需)",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="节点名称 (必需)",
    )
    parser.add_argument(
        "--token",
        required=True,
        help="认证 Token (必需)",
    )
    parser.add_argument(
        "--region",
        help="区域标识 (可选, 如 HK/SG/US)",
    )
    parser.add_argument(
        "--endpoint-url",
        help="出口公网地址 (可选, 用于延迟探测)",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=int,
        default=20,
        help="心跳间隔秒数 (默认 20)",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    agent = LLMAgent(
        ws_url=args.ws_url,
        heartbeat_url=args.heartbeat_url,
        name=args.name,
        token=args.token,
        region=args.region,
        endpoint_url=args.endpoint_url,
        heartbeat_interval=args.heartbeat_interval,
    )

    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        print(f"[{args.name}] Interrupted")


if __name__ == "__main__":
    main()
