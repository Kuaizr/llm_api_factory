import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request

app = FastAPI(title="Mock LLM API")


def _parse_keys(raw: str) -> set[str]:
    keys = [key.strip() for key in raw.split(",") if key.strip()]
    return set(keys)


SERVICE_NAME = os.getenv("MOCK_SERVICE_NAME", "mock-a")
VALID_KEYS = _parse_keys(os.getenv("MOCK_API_KEYS", "mock-key-1,mock-key-2"))
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
    return api_key or None


def _require_key(request: Request) -> str:
    key = _extract_key(request)
    if not key or key not in VALID_KEYS:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return key


@app.get("/")
async def root() -> dict[str, Any]:
    return {"service": SERVICE_NAME, "status": "ok"}


@app.get("/v1/models")
async def list_models(request: Request) -> dict[str, Any]:
    _require_key(request)
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


@app.post("/v1/embeddings")
async def embeddings(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_key(request)
    model = str(payload.get("model") or MODEL_IDS[2])
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


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": SERVICE_NAME}
