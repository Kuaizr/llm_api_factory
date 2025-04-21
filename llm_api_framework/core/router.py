from typing import List
from .client import APIClient
from .factory import PlatformFactory
from ..utils.config_manager import ConfigManager
from ..utils.logger import Logger
from .error_types import ErrorType, Action

class APIClientRouter:
    """管理API客户端路由和切换"""
    def __init__(self, config_path: str):
        self.config = ConfigManager(config_path)
        self.clients: List[APIClient] = []
        self._current_index = 0
        self._initialize_clients()
        
    def get_max_retries(self) -> int:
        """获取配置的最大重试次数"""
        return self.config.get_max_retries()
    
    def _initialize_clients(self):
        """根据配置初始化客户端"""
        config = self.config.load_config()
        for platform in self.config.get_platform_order():
            platform_config = self.config.get_platform_config(platform)
            api_keys = self.config.get_api_keys(platform)
            model = self.config.get_model_name(platform)
            
            for api_key in api_keys:
                try:
                    client = PlatformFactory.create_client(
                        platform, 
                        api_key,
                        model=model,
                        base_url=platform_config.get("base_url")
                    )
                    self.clients.append(client)
                except Exception as e:
                    Logger().log_error(e)
    
    def get_current_client(self) -> APIClient:
        """获取当前客户端(严格遵循配置中的平台顺序)"""
        return self.clients[self._current_index]

    def process_error(self, error_type: ErrorType) -> Action:
        """根据错误类型返回处理动作"""
        return self.config.get_error_policy(error_type)
    
    def switch_client(self):
        """切换到下一个可用客户端"""
        self._current_index = (self._current_index + 1) % len(self.clients)