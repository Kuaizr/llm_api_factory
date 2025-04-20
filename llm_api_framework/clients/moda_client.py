from typing import Dict, Any, Generator, AsyncGenerator, List, Optional
import openai
from openai import AsyncOpenAI
from ..core.client import APIClient
from ..core.error_types import ErrorType

class MoDaClient(APIClient):
    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        super().__init__(api_key, base_url)
        self.model = model
        self.sync_client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url or "https://api-inference.modelscope.cn/v1/"
        )
        self.async_client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or "https://api-inference.modelscope.cn/v1/"
        )
        
    def get_default_url(self) -> str:
        return "https://api-inference.modelscope.cn/v1/"
        
    def call_api(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        try:
            response = self.sync_client.chat.completions.create(
                model=self.model,
                messages=messages,
                **kwargs
            )
            return {
                "content": response.choices[0].message.content,
                "usage": dict(response.usage),
                "model": response.model
            }
        except Exception as e:
            return {"error": str(e)}
            
    async def call_api_async(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        try:
            response = await self.async_client.chat.completions.create(
                model=self.model,
                messages=messages,
                **kwargs
            )
            return {
                "content": response.choices[0].message.content,
                "usage": dict(response.usage),
                "model": response.model
            }
        except Exception as e:
            return {"error": str(e)}
            
    def stream_api(self, messages: List[Dict[str, Any]], **kwargs) -> Generator[Dict[str, Any], None, None]:
        try:
            stream = self.sync_client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                **kwargs
            )
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield {"content": chunk.choices[0].delta.content}
        except Exception as e:
            yield {"error": str(e)}
            
    async def stream_api_async(self, messages: List[Dict[str, Any]], **kwargs) -> AsyncGenerator[Dict[str, Any], None]:
        try:
            stream = await self.async_client.chat.completions.create(
                model=self.model,
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
        if not isinstance(response, dict) or "error" not in response:
            return ErrorType.OTHER
        
        error_msg = str(response["error"]).lower()
        
        if "network" in error_msg or "timeout" in error_msg:
            return ErrorType.NETWORK
        elif "rate limit" in error_msg:
            return ErrorType.RATE_LIMIT
        elif "quota" in error_msg:
            return ErrorType.QUOTA_EXCEEDED
        elif "invalid" in error_msg or "auth" in error_msg:
            return ErrorType.AUTH_FAILURE
        elif "server" in error_msg or "internal" in error_msg:
            return ErrorType.SERVER_ERROR
        return ErrorType.OTHER