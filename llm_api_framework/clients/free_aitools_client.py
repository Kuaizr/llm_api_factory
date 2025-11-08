from typing import Optional
from .base_requests_client import BaseRequestsChatCompletionsClient

class FreeAitoolsClient(BaseRequestsChatCompletionsClient):
    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        super().__init__(api_key, model, base_url)
        
    def get_default_url(self) -> str:
        return "https://platform.aitools.cfd/api/v1"