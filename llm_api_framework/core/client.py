from abc import ABC, abstractmethod
from typing import Dict, Any, Generator, AsyncGenerator, List, Optional
import requests

class APIClient(ABC):
    def __init__(self, api_key: str, base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = base_url or self.get_default_url()
        
    @abstractmethod
    def get_default_url(self) -> str:
        """Get default API endpoint URL for the platform"""
        pass
        
    @abstractmethod
    def call_api(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """Call the API with given messages (sync)"""
        pass
        
    @abstractmethod
    async def call_api_async(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """Call the API with given messages (async)"""
        pass
        
    @abstractmethod
    def stream_api(self, messages: List[Dict[str, str]], **kwargs) -> Generator[Dict[str, Any], None, None]:
        """Stream API response (sync)"""
        pass
        
    @abstractmethod 
    async def stream_api_async(self, messages: List[Dict[str, str]], **kwargs) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream API response (async)"""
        pass

    def handle_error(self, response: requests.Response) -> bool:
        """
        Handle API error response
        Returns True if should retry, False otherwise
        """
        if response.status_code >= 500:
            return True  # Server error, should retry
        return False