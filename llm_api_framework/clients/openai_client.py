from typing import Optional, Dict, Any, Generator, AsyncGenerator, List
from ..core.client import APIClient
import openai
from openai import AsyncOpenAI

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
            
    def handle_error(self, response: Dict[str, Any]) -> bool:
        if "error" in response:
            error_msg = response["error"].lower()
            if "rate limit" in error_msg:
                return True  # Should retry with different key
            elif "quota" in error_msg:
                return False  # Should switch platform
        return super().handle_error(response)