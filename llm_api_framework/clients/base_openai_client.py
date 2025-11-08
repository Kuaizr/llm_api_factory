from typing import Dict, Any, Generator, AsyncGenerator, List, Optional
import openai
from openai import AsyncOpenAI
from ..core.client import APIClient
from ..core.error_types import ErrorType


class BaseOpenAIChatCompletionsClient(APIClient):
    """
    基于 openai SDK 的 /chat/completions 通用客户端基类
    子类可在构造中设置 self.model（可选），或在调用时传入 model
    """
    def __init__(self, api_key: str, base_url: Optional[str] = None):
        super().__init__(api_key, base_url)
        self.sync_client = openai.OpenAI(api_key=api_key, base_url=self.base_url)
        self.async_client = AsyncOpenAI(api_key=api_key, base_url=self.base_url)

    def call_api(self, messages: List[Dict[str, Any]], model: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        final_model = model or getattr(self, "model", "gpt-3.5-turbo")
        try:
            response = self.sync_client.chat.completions.create(
                model=final_model,
                messages=messages,
                **kwargs
            )
            if not response.choices:
                return {"error": "Empty response"}
            return response.choices[0].message.model_dump()
        except Exception as e:
            return {"error": str(e)}

    async def call_api_async(self, messages: List[Dict[str, Any]], model: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        final_model = model or getattr(self, "model", "gpt-3.5-turbo")
        try:
            response = await self.async_client.chat.completions.create(
                model=final_model,
                messages=messages,
                **kwargs
            )
            if not response.choices:
                return {"error": "Empty response"}
            return response.choices[0].message.model_dump()
        except Exception as e:
            return {"error": str(e)}

    def stream_api(self, messages: List[Dict[str, Any]], model: Optional[str] = None, **kwargs) -> Generator[Dict[str, Any], None, None]:
        final_model = model or getattr(self, "model", "gpt-3.5-turbo")
        try:
            stream = self.sync_client.chat.completions.create(
                model=final_model,
                messages=messages,
                stream=True,
                **kwargs
            )
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield {"content": chunk.choices[0].delta.content}
        except Exception as e:
            yield {"error": str(e)}

    async def stream_api_async(self, messages: List[Dict[str, Any]], model: Optional[str] = None, **kwargs) -> AsyncGenerator[Dict[str, Any], None]:
        final_model = model or getattr(self, "model", "gpt-3.5-turbo")
        try:
            stream = await self.async_client.chat.completions.create(
                model=final_model,
                messages=messages,
                stream=True,
                **kwargs
            )
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield {"content": chunk.choices[0].delta.content}
        except Exception as e:
            yield {"error": str(e)}

    def handle_error(self, response: Any) -> ErrorType:
        if isinstance(response, str):
            error_msg = response.lower()
        elif isinstance(response, dict):
            error_msg = str(response.get("error", "")).lower()
        else:
            return ErrorType.OTHER
        if "rate limit" in error_msg:
            return ErrorType.RATE_LIMIT
        if "quota" in error_msg:
            return ErrorType.QUOTA_EXCEEDED
        if "invalid" in error_msg or "auth" in error_msg or "token" in error_msg:
            return ErrorType.AUTH_FAILURE
        if "server" in error_msg or "internal" in error_msg or "overloaded" in error_msg:
            return ErrorType.SERVER_ERROR
        if "network" in error_msg or "timeout" in error_msg:
            return ErrorType.NETWORK
        if "not found" in error_msg:
            return ErrorType.INVALID_REQUEST
        return ErrorType.OTHER

