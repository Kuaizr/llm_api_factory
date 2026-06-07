import os
import time
from typing import Any
import json

from fastapi import FastAPI, HTTPException, Request

app = FastAPI(title="Mock LLM API")


def _parse_keys(raw: str) -> set[str]:
    keys = [key.strip() for key in raw.split(",") if key.strip()]
    return set(keys)


def _parse_model_ids(raw: str) -> list[str]:
    return [model.strip() for model in raw.split(",") if model.strip()]


SERVICE_NAME = os.getenv("MOCK_SERVICE_NAME") or os.getenv("SERVICE_NAME", "mock-a")
VALID_KEYS = _parse_keys(os.getenv("MOCK_API_KEYS", "mock-key-1,mock-key-2"))
MODEL_IDS = _parse_model_ids(os.getenv("MOCK_MODEL_IDS") or os.getenv("MODEL_IDS", ""))
if not MODEL_IDS:
    MODEL_IDS = [
        f"{SERVICE_NAME}-gpt-4",
        f"{SERVICE_NAME}-gpt-3.5",
        f"{SERVICE_NAME}-embed-1",
    ]


def _extract_key(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    api_key = request.headers.get("X-API-Key", "").strip()
    if api_key:
        return api_key
    api_key = request.headers.get("x-api-key", "").strip()
    if api_key:
        return api_key
    api_key = request.headers.get("x-goog-api-key", "").strip()
    if api_key:
        return api_key
    api_key = request.query_params.get("key", "").strip()
    return api_key or None


def _require_key(request: Request) -> str:
    key = _extract_key(request)
    if not key or key not in VALID_KEYS:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return key


async def _log_request(request: Request, protocol: str, model: str | None = None) -> None:
    body = await request.body()
    record = {
        "service": SERVICE_NAME,
        "protocol": protocol,
        "method": request.method,
        "path": request.url.path,
        "query": str(request.url.query),
        "model": model,
        "trace_id": request.headers.get("x-trace-id"),
        "body_bytes": len(body),
    }
    print(json.dumps(record, ensure_ascii=False), flush=True)


@app.get("/")
async def root() -> dict[str, Any]:
    return {"service": SERVICE_NAME, "status": "ok"}


@app.get("/v1/models")
async def list_models(request: Request) -> dict[str, Any]:
    _require_key(request)
    await _log_request(request, "openai")
    created = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": model_id, "object": "model", "created": created, "owned_by": SERVICE_NAME}
            for model_id in MODEL_IDS
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_key(request)
    model = str(payload.get("model") or MODEL_IDS[0])
    await _log_request(request, "openai", model)
    messages = payload.get("messages") or []
    content = "mock response"
    if isinstance(messages, list) and messages:
        last = messages[-1]
        if isinstance(last, dict):
            content = str(last.get("content") or content)
    created = int(time.time())
    return {
        "id": f"chatcmpl-{created}",
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": f"{SERVICE_NAME}: {content}"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 12, "total_tokens": 22},
    }


@app.post("/v1/completions")
async def completions(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_key(request)
    model = str(payload.get("model") or MODEL_IDS[1])
    await _log_request(request, "openai", model)
    prompt = payload.get("prompt")
    text = str(prompt) if prompt is not None else "mock completion"
    created = int(time.time())
    return {
        "id": f"cmpl-{created}",
        "object": "text_completion",
        "created": created,
        "model": model,
        "choices": [
            {"index": 0, "text": f"{SERVICE_NAME}: {text}", "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 9, "total_tokens": 17},
    }


@app.post("/v1/responses")
async def responses(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_key(request)
    model = str(payload.get("model") or MODEL_IDS[0])
    await _log_request(request, "openai", model)
    prompt = payload.get("input")
    if isinstance(prompt, list) and prompt:
        prompt = prompt[-1]
    text = str(prompt) if prompt is not None else "mock response"
    created = int(time.time())
    return {
        "id": f"resp-{created}",
        "object": "response",
        "created_at": created,
        "model": model,
        "output": [
            {
                "id": f"msg-{created}",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": f"{SERVICE_NAME}: {text}"}
                ],
            }
        ],
        "usage": {"input_tokens": 9, "output_tokens": 11, "total_tokens": 20},
    }


@app.post("/v1/embeddings")
async def embeddings(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_key(request)
    model = str(payload.get("model") or MODEL_IDS[2])
    await _log_request(request, "openai", model)
    created = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "object": "embedding",
                "index": 0,
                "embedding": [0.1, 0.2, 0.3, 0.4],
            }
        ],
        "model": model,
        "usage": {"prompt_tokens": 4, "total_tokens": 4},
        "created": created,
    }


@app.post("/v1/messages")
async def anthropic_messages(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_key(request)
    model = str(payload.get("model") or f"{SERVICE_NAME}-claude")
    await _log_request(request, "anthropic", model)
    messages = payload.get("messages") or []
    content = "mock response"
    if isinstance(messages, list) and messages:
        last = messages[-1]
        if isinstance(last, dict):
            content = str(last.get("content") or content)
    created = int(time.time())
    return {
        "id": f"msg-{created}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": f"{SERVICE_NAME}: {content}"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 12},
    }


@app.api_route("/v1beta/{path:path}", methods=["GET", "POST"])
async def gemini_passthrough(path: str, request: Request) -> dict[str, Any]:
    _require_key(request)
    if request.method == "GET" and path == "models":
        await _log_request(request, "gemini")
        return {
            "models": [
                {
                    "name": f"models/{model_id}",
                    "version": model_id,
                    "displayName": model_id,
                    "supportedGenerationMethods": [
                        "generateContent",
                        "streamGenerateContent",
                        "countTokens",
                    ],
                }
                for model_id in MODEL_IDS
            ]
        }

    model = None
    if path.startswith("models/") and ":" in path:
        model = path.removeprefix("models/").split(":", 1)[0]
    await _log_request(request, "gemini", model)
    payload = await request.json()
    text = "mock response"
    contents = payload.get("contents") if isinstance(payload, dict) else None
    if isinstance(contents, list) and contents:
        first = contents[-1]
        if isinstance(first, dict):
            parts = first.get("parts")
            if isinstance(parts, list) and parts:
                part = parts[0]
                if isinstance(part, dict) and part.get("text"):
                    text = str(part["text"])
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": f"{SERVICE_NAME}: {text}"}],
                },
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 12,
            "totalTokenCount": 22,
        },
        "modelVersion": model or f"{SERVICE_NAME}-gemini",
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": SERVICE_NAME}
