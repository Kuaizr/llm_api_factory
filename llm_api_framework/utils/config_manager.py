import json
from pathlib import Path
from typing import Dict, List, Optional, Callable
import os
import threading
import atexit
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from ..core.error_types import ErrorType
from ..core.error_types import Action
from ..utils.logger import Logger

class ConfigFileHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable):
        self.callback = callback
    
    def on_modified(self, event):
        if not event.is_directory:
            self.callback()

class ConfigManager:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, config_path: str = "configs/default.json"):
        """
        配置文件管理器
        
        参数:
            config_path: JSON配置文件路径，默认为configs/default.json
        """
        self.config_path = Path(config_path)
        self._config = self.load_config()
        self._observer = None
        self._handlers = []
        self._start_watching()
        atexit.register(self.stop_watching)
        
    def stop_watching(self):
        """停止配置文件监听"""
        if self._observer and self._observer.is_alive():
            self._observer.stop()
            self._observer.join()
        
    def _start_watching(self):
        """启动配置文件监听"""
        if self._observer is None:
            self._observer = Observer()
            handler = ConfigFileHandler(self.reload_config)
            self._observer.schedule(handler, self.config_path.parent, recursive=False)
            self._observer.start()
            
    def reload_config(self):
        """重新加载配置文件"""
        try:
            new_config = self.load_config()
            with self._lock:
                self._config = new_config
            for handler in self._handlers:
                handler()
        except Exception as e:
            Logger().log_error(f"Failed to reload config: {e}")
            
    def register_change_handler(self, handler: Callable):
        """注册配置变更回调函数"""
        self._handlers.append(handler)

    def load_config(self) -> Dict:
        """加载JSON配置文件"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            _config = json.load(f)
        return _config

    def save_config(self, config: Dict):
        """保存配置到JSON文件"""
        os.makedirs(self.config_path.parent, exist_ok=True)
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        self._config = config

    def get_platform_order(self) -> List[str]:
        """获取平台调用顺序列表"""
        return self._config.get("platform_order", [])

    def get_platform_config(self, platform: str) -> Dict:
        """获取指定平台的配置"""
        return self._config.get("platforms", {}).get(platform, {})
        
    def get_model_name(self, platform: str) -> Optional[str]:
        """获取平台对应的模型名称"""
        return self.get_platform_config(platform).get("model")
        
    def get_api_keys(self, platform: str) -> List[str]:
        """获取平台的API密钥列表"""
        return self.get_platform_config(platform).get("api_keys", [])

    def get_system_prompt(self) -> Optional[str]:
        """获取系统提示语"""
        return self._config.get("system_prompt")
    
    def get_vision(self) -> bool:
        """获取是否启用视觉输入"""
        return self._config.get("vision", False)
        
    def get_max_retries(self) -> int:
        """获取最大重试次数"""
        return self._config.get("max_retries", 3)
        
    def get_error_policy(self, error_type: ErrorType) -> Action:
        """获取指定错误类型的处理策略"""
        policy_map = {
            "network": ErrorType.NETWORK,
            "rate_limit": ErrorType.RATE_LIMIT,
            "quota_exceeded": ErrorType.QUOTA_EXCEEDED,
            "auth_failure": ErrorType.AUTH_FAILURE,
            "invalid_request": ErrorType.INVALID_REQUEST
        }
        
        policies = self._config.get("error_policies", {})
        policy_str = policies.get(error_type.name.lower(), "abort")
        
        return {
            "retry": Action.RETRY,
            "switch": Action.SWITCH,
            "abort": Action.ABORT
        }.get(policy_str.lower(), Action.ABORT)