from typing import Optional
from .base_openai_client import BaseOpenAIChatCompletionsClient

class MoDaClient(BaseOpenAIChatCompletionsClient):
    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        super().__init__(api_key, base_url or "https://api-inference.modelscope.cn/v1/")
        self.model = model
        
    def get_default_url(self) -> str:
        return "https://api-inference.modelscope.cn/v1/"