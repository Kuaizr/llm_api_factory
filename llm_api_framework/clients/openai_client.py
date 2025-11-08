from typing import Optional
from .base_openai_client import BaseOpenAIChatCompletionsClient

class OpenAIClient(BaseOpenAIChatCompletionsClient):
    def __init__(self, api_key: str, base_url: Optional[str] = None):
        super().__init__(api_key, base_url)
        
    def get_default_url(self) -> str:
        return "https://api.openai.com/v1"