from typing import Dict, Any, Generator, AsyncGenerator, List, Optional
import requests
import aiohttp
import json
from ..core.client import APIClient
from ..core.error_types import ErrorType


class BaseRequestsChatCompletionsClient(APIClient):
    """
    基于 requests/aiohttp 的 /chat/completions 通用客户端基类
    子类只需实现 get_default_url()，并在构造函数中传入 model
    """
    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        super().__init__(api_key, base_url)
        self.model = model

    def call_api(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {"model": self.model, "messages": messages, **kwargs}
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            message = self._extract_message(data)
            if message is None:
                return {"error": "Invalid response format"}
            return message
        except Exception as e:
            return {"error": str(e)}

    async def call_api_async(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {"model": self.model, "messages": messages, **kwargs}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=30
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    message = self._extract_message(data)
                    if message is None:
                        return {"error": "Invalid response format"}
                    return message
        except Exception as e:
            return {"error": str(e)}

    def stream_api(self, messages: List[Dict[str, Any]], **kwargs) -> Generator[Dict[str, Any], None, None]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {"model": self.model, "messages": messages, "stream": True, **kwargs}
        try:
            with requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                stream=True,
                timeout=30
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    decoded_line = line.decode('utf-8')
                    if not decoded_line.startswith("data: "):
                        continue
                    try:
                        json_data = json.loads(decoded_line[6:])
                        if json_data.get("choices"):
                            delta = json_data["choices"][0]["delta"]
                            content = delta.get("reasoning_content") or delta.get("content")
                            if content:
                                yield {"content": content}
                    except json.JSONDecodeError:
                        if decoded_line == "data: [DONE]":
                            break
        except Exception as e:
            yield {"error": str(e)}

    async def stream_api_async(self, messages: List[Dict[str, Any]], **kwargs) -> AsyncGenerator[Dict[str, Any], None]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {"model": self.model, "messages": messages, "stream": True, **kwargs}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=30
                ) as response:
                    async for line in response.content:
                        decoded_line = line.decode('utf-8')
                        if not decoded_line.startswith("data: "):
                            continue
                        try:
                            json_data = json.loads(decoded_line[6:])
                            if json_data.get("choices"):
                                delta = json_data["choices"][0]["delta"]
                                content = delta.get("reasoning_content") or delta.get("content")
                                if content:
                                    yield {"content": content}
                        except json.JSONDecodeError:
                            if decoded_line == "data: [DONE]":
                                break
        except Exception as e:
            yield {"error": str(e)}

    def handle_error(self, response: Any) -> ErrorType:
        if isinstance(response, str):
            error_msg = response.lower()
        elif isinstance(response, dict):
            error_msg = str(response.get("message", "") or response.get("error", "")).lower()
        else:
            return ErrorType.OTHER
        if "rate limit" in error_msg or "tpm limit" in error_msg:
            return ErrorType.RATE_LIMIT
        if "invalid token" in error_msg or "auth" in error_msg:
            return ErrorType.AUTH_FAILURE
        if "service overloaded" in error_msg or "try again later" in error_msg or "server" in error_msg:
            return ErrorType.SERVER_ERROR
        if "timeout" in error_msg or "network" in error_msg:
            return ErrorType.NETWORK
        if "not found" in error_msg or "page not found" in error_msg:
            return ErrorType.INVALID_REQUEST
        return ErrorType.OTHER

    def _extract_message(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        choices = payload.get("choices")
        if not choices:
            return None
        message = choices[0].get("message")
        if isinstance(message, dict):
            return message
        return None

