import json
from pathlib import Path
from typing import Dict, List, Optional
import os

class ConfigManager:
    def __init__(self, config_path: str = "configs/default.json"):
        """
        配置文件管理器
        
        参数:
            config_path: JSON配置文件路径，默认为configs/default.json
        """
        self.config_path = Path(config_path)
        self._config = self.load_config()

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