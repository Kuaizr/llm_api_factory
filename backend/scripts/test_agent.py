#!/usr/bin/env python3
"""
Simple test agent that connects to the backend WebSocket.
Run from the backend directory: python -m app.services.test_agent_client

Usage:
    python test_agent.py --ws-url ws://localhost:8000/agent/ws \
                         --heartbeat-url http://localhost:8000/agent/heartbeat \
                         --name "测试节点" \
                         --token "your-token-here"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from typing import Any

import httpx
import websockets


async def probe_capabilities() -> dict[str, Any]:
    """Probe API capabilities (simplified)."""
    # Simplified - just report as supported for testing
    return {
        "supports_gpt": True,
        "supports_gemini": True, 
        "supports_claude": True,
        "probe_latency_ms": 100,
    }


async def heartbeat_task(
    ws: websockets.WebSocketClientProtocol,
    interval: int = 20
) -> None:
    """Send periodic heartbeats over WebSocket."""
    while True:
        await asyncio.sleep(interval)
        try:
            await ws.send(json.dumps({"type": "heartbeat"}))
        except Exception:
            break


async def http_heartbeat_task(
    heartbeat_url: str,
    name: str,
    token: str | None,
    capabilities: dict[str, Any],
    interval: int = 20
) -> None:
    """Send periodic HTTP heartbeats."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    payload = {
        "name": name,
        "token": token,
        **capabilities
    }
    
    while True:
        await asyncio.sleep(interval)
        try:
            async with httpx.AsyncClient() as client:
                await client.post(heartbeat_url, json=payload, headers=headers)
        except Exception:
            pass


async def run_agent(
    ws_url: str,
    heartbeat_url: str | None,
    name: str,
    token: str | None = None,
    region: str | None = None,
    endpoint_url: str | None = None,
) -> None:
    """Connect to the backend and handle proxy requests."""
    headers = []
    if token:
        headers.append(("Authorization", f"Bearer {token}"))
    
    capabilities = await probe_capabilities()
    
    print(f"Connecting to {ws_url} as '{name}'...")
    print(f"Region: {region}, Endpoint: {endpoint_url}")
    print(f"Capabilities: {capabilities}")
    
    while True:
        try:
            async with websockets.connect(ws_url, additional_headers=dict(headers)) as ws:
                # Register with the server
                register_payload = {
                    "type": "register",
                    "name": name,
                    "region": region,
                    "endpoint_url": endpoint_url,
                    "token": token,
                    **capabilities
                }
                await ws.send(json.dumps(register_payload))
                print(f"Registered as '{name}'")
                
                # Start heartbeat tasks
                ws_heartbeat = asyncio.create_task(heartbeat_task(ws))
                http_heartbeat = None
                if heartbeat_url:
                    http_heartbeat = asyncio.create_task(
                        http_heartbeat_task(
                            heartbeat_url, name, token, capabilities
                        )
                    )
                
                # Handle messages
                try:
                    async for raw_message in ws:
                        if not raw_message:
                            continue
                        try:
                            payload = json.loads(raw_message)
                        except json.JSONDecodeError:
                            continue
                        
                        msg_type = payload.get("type")
                        if msg_type == "heartbeat":
                            continue
                        elif msg_type == "error":
                            # Print error but don't disconnect
                            print(f"Server error: {payload.get('error', 'unknown')}")
                            continue
                        elif msg_type == "registered":
                            print(f"Registration confirmed by server")
                            continue
                        elif msg_type == "proxy_request":
                            # Handle proxy request - forward to actual endpoint
                            request_id = payload.get("request_id")  # Fixed: was "id"
                            method = payload.get("method", "POST")
                            url = payload.get("url")
                            headers = payload.get("headers", {})
                            body_b64 = payload.get("body", "")
                            
                            print(f"DEBUG: headers = {headers}")
                            print(f"DEBUG: url = {url}")
                            
                            # Decode body from base64
                            import base64
                            try:
                                body = base64.b64decode(body_b64) if body_b64 else b""
                            except:
                                body = body_b64.encode() if isinstance(body_b64, str) else b""
                            
                            print(f"Received proxy request: {request_id}, url: {url}")
                            
                            # Forward the request to the actual endpoint
                            try:
                                async with httpx.AsyncClient(timeout=60.0) as client:
                                    if method == "GET":
                                        resp = await client.get(url, headers=headers)
                                    else:
                                        resp = await client.request(method, url, headers=headers, content=body)
                                    
                                    response_headers = dict(resp.headers)
                                    response_headers["content-type"] = resp.headers.get("content-type", "application/json")
                                    
                                    # Encode response body to base64
                                    import base64
                                    body_b64 = base64.b64encode(resp.text.encode()).decode()
                                    
                                    await ws.send(json.dumps({
                                        "type": "proxy_response",
                                        "request_id": request_id,  # Fixed: was "id"
                                        "status_code": resp.status_code,  # Fixed: was "status"
                                        "headers": response_headers,
                                        "body": body_b64,
                                    }))
                            except Exception as e:
                                print(f"Error forwarding request: {e}")
                                import base64
                                error_body = base64.b64encode(f"Agent error: {str(e)}".encode()).decode()
                                await ws.send(json.dumps({
                                    "type": "proxy_response",
                                    "request_id": request_id,
                                    "status_code": 500,
                                    "headers": {"content-type": "text/plain"},
                                    "body": error_body,
                                }))
                        else:
                            print(f"Unknown message type: {msg_type}")
                except websockets.ConnectionClosed:
                    print("Connection closed, reconnecting...")
                finally:
                    ws_heartbeat.cancel()
                    if http_heartbeat:
                        http_heartbeat.cancel()
                    try:
                        await ws_heartbeat
                    except asyncio.CancelledError:
                        pass
                    
        except Exception as e:
            print(f"Error: {e}, reconnecting in 5 seconds...")
            await asyncio.sleep(5)


def main():
    parser = argparse.ArgumentParser(description="Test Agent Client")
    parser.add_argument("--ws-url", required=True, help="WebSocket URL")
    parser.add_argument("--heartbeat-url", help="HTTP heartbeat URL (optional)")
    parser.add_argument("--name", required=True, help="Agent name")
    parser.add_argument("--token", help="Auth token")
    parser.add_argument("--region", help="Region")
    parser.add_argument("--endpoint-url", help="Endpoint URL")
    
    args = parser.parse_args()
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    def signal_handler(sig, frame):
        print("\nShutting down...")
        loop.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        loop.run_until_complete(run_agent(
            ws_url=args.ws_url,
            heartbeat_url=args.heartbeat_url,
            name=args.name,
            token=args.token,
            region=args.region,
            endpoint_url=args.endpoint_url,
        ))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
