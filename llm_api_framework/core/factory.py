from typing import Type, Optional
from ..clients.openai_client import OpenAIClient
from ..clients.moda_client import MoDaClient
from ..clients.siliconflow_client import SiliconFlowClient
from ..clients.free_aitools_client import FreeAitoolsClient
from ..clients.infini_client import InfiniClient
from .client import APIClient

class PlatformFactory:
    _client_classes = {
        "openai": OpenAIClient,
        "moda": MoDaClient,
        "siliconflow": SiliconFlowClient,
        "free_aitools": FreeAitoolsClient,
        "infini": InfiniClient
    }

    @classmethod
    def create_client(cls, platform: str, api_key: str, model: Optional[str] = None, **kwargs) -> APIClient:
        """创建指定平台的API客户端"""
        client_class = cls._client_classes.get(platform.lower())
        if not client_class:
            raise ValueError(f"Unsupported platform: {platform}")
            
        # 特殊处理需要model参数的平台
        if platform.lower() in ["moda", "siliconflow", "free_aitools", "infini"] and model:
            return client_class(api_key, model=model, **kwargs)
            
        return client_class(api_key, **kwargs)

    @classmethod
    def register_platform(cls, platform: str, client_class: Type[APIClient]):
        """注册新的平台客户端类"""
        if not issubclass(client_class, APIClient):
            raise TypeError("Client class must inherit from APIClient")
        cls._client_classes[platform.lower()] = client_class