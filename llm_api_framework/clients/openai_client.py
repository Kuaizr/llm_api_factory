from typing import Optional, Dict, Any, Generator, AsyncGenerator, List
from ..core.client import APIClient
import openai
from openai import AsyncOpenAI
from ..core.error_types import ErrorType

class OpenAIClient(APIClient):
    def __init__(self, api_key: str, base_url: Optional[str] = None):
        super().__init__(api_key, base_url)
        self.sync_client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self.async_client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        
    def get_default_url(self) -> str:
        return "https://api.openai.com/v1"
        
    def call_api(self, messages: List[Dict[str, str]], model: str = "gpt-3.5-turbo", **kwargs) -> Dict[str, Any]:
        try:
            response = self.sync_client.chat.completions.create(
                model=model,
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
            
    def stream_api(self, messages: List[Dict[str, str]], model: str = "gpt-3.5-turbo", **kwargs) -> Generator[Dict[str, Any], None, None]:
        try:
            stream = self.sync_client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                **kwargs
            )
            for chunk in stream:
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
        elif "quota" in error_msg:
            return ErrorType.QUOTA_EXCEEDED
        elif "invalid" in error_msg or "auth" in error_msg or "token" in error_msg:
            return ErrorType.AUTH_FAILURE
        elif "server" in error_msg or "internal" in error_msg or "overloaded" in error_msg:
            return ErrorType.SERVER_ERROR
        elif "network" in error_msg or "timeout" in error_msg:
            return ErrorType.NETWORK
        elif "not found" in error_msg:
            return ErrorType.INVALID_REQUEST
        return ErrorType.OTHER