from typing import Dict, Any, Generator, AsyncGenerator, List, Optional, Union
from ..utils.config_manager import ConfigManager
from ..utils.logger import Logger
from ..utils.monitor import Monitor
from .factory import PlatformFactory
from .client import APIClient
import time
import asyncio

class APIManager:
    def __init__(self, config_path: str = "configs/default.json"):
        """
        API管理器，负责:
        - 管理多个平台客户端
        - 维护对话上下文
        - 处理API调用和错误重试
        
        参数:
            config_path: 配置文件路径，默认为configs/default.json
        """
        self.config = ConfigManager(config_path)
        self.logger = Logger()
        self.monitor = Monitor()
        self.clients: List[APIClient] = []
        self._current_client_index = 0
        self.messages: List[Dict[str, str]] = []
        
        # 初始化系统提示和客户端
        if system_prompt := self.config.get_system_prompt():
            self.messages.append({"role": "system", "content": system_prompt})
        self._initialize_clients()

    def _initialize_clients(self):
        """根据配置文件初始化所有平台客户端"""
        config = self.config.load_config()
        
        for platform in self.config.get_platform_order():
            platform_config = self.config.get_platform_config(platform)
            print
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
                    self.logger.log_error(e)

    def add_message(self, role: str, content: str):
        """
        添加消息到对话历史
        
        参数:
            role: 消息角色 (user/assistant/system)
            content: 消息内容
        """
        self.messages.append({"role": role, "content": content})

    def clear_messages(self, keep_system_prompt: bool = True):
        """
        清除对话历史
        
        参数:
            keep_system_prompt: 是否保留系统提示
        """
        if keep_system_prompt and len(self.messages) > 0 and self.messages[0]["role"] == "system":
            self.messages = [self.messages[0]]
        else:
            self.messages = []

    def call_api(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        同步API调用
        
        参数:
            prompt: 用户提示
            **kwargs: 其他API参数
            
        返回:
            API响应字典
        """
        if not self.clients:
            return {"error": "No available API clients"}
            
        self.add_message("user", prompt)
        client = self.clients[self._current_client_index]
        
        try:
            start_time = time.time()
            response = client.call_api(self.messages, **kwargs)
            latency = time.time() - start_time
            
            self.monitor.record_latency(client, latency)
            
            if "error" not in response:
                # 处理不同API的响应格式
                if "choices" in response and len(response["choices"]) > 0:
                    content = response["choices"][0]["message"]["content"]
                    self.add_message("assistant", content)
                    response["content"] = content
                elif "content" in response:
                    self.add_message("assistant", response["content"])
                
                self.monitor.record_success(client)
                return response
                
            self.monitor.record_failure(client)
            return response
            
        except Exception as e:
            self.monitor.record_failure(client)
            self.logger.log_error(e)
            return {"error": str(e)}

    async def call_api_async(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """异步API调用，参数和返回值同call_api"""
        if not self.clients:
            return {"error": "No available API clients"}
            
        self.add_message("user", prompt)
        client = self.clients[self._current_client_index]
        
        try:
            start_time = time.time()
            if hasattr(client, 'call_api_async'):
                response = await client.call_api_async(self.messages, **kwargs)
            else:
                response = client.call_api(self.messages, **kwargs)
                
            latency = time.time() - start_time
            self.monitor.record_latency(client, latency)
            
            if "error" not in response:
                # 处理不同API的响应格式
                if "choices" in response and len(response["choices"]) > 0:
                    content = response["choices"][0]["message"]["content"]
                    self.add_message("assistant", content)
                    response["content"] = content
                elif "content" in response:
                    self.add_message("assistant", response["content"])
                
                self.monitor.record_success(client)
                return response
                
            self.monitor.record_failure(client)
            return response
            
        except Exception as e:
            self.monitor.record_failure(client)
            self.logger.log_error(e)
            return {"error": str(e)}

    def stream_api(self, prompt: str, **kwargs) -> Generator[Dict[str, Any], None, None]:
        """
        同步流式API调用
        
        参数:
            prompt: 用户提示
            **kwargs: 其他API参数
            
        返回:
            生成器，产生流式响应块
        """
        if not self.clients:
            yield {"error": "No available API clients"}
            return
            
        self.add_message("user", prompt)
        client = self.clients[self._current_client_index]
        full_response = ""
        
        try:
            for chunk in client.stream_api(self.messages, **kwargs):
                if "content" in chunk:
                    full_response += chunk["content"]
                    yield chunk
                elif "error" in chunk:
                    yield chunk
                    return
                    
            self.add_message("assistant", full_response)
            
        except Exception as e:
            yield {"error": str(e)}

    async def stream_api_async(self, prompt: str, **kwargs) -> AsyncGenerator[Dict[str, Any], None]:
        """异步流式API调用，参数和返回值同stream_api"""
        if not self.clients:
            yield {"error": "No available API clients"}
            return
            
        self.add_message("user", prompt)
        client = self.clients[self._current_client_index]
        full_response = ""
        
        try:
            if hasattr(client, 'stream_api_async'):
                stream = client.stream_api_async(self.messages, **kwargs)
            else:
                stream = client.stream_api(self.messages, **kwargs)
                
            async for chunk in stream:
                if "content" in chunk:
                    full_response += chunk["content"]
                    yield chunk
                elif "error" in chunk:
                    yield chunk
                    return
                    
            self.add_message("assistant", full_response)
            
        except Exception as e:
            yield {"error": str(e)}

    def get_conversation_history(self) -> List[Dict[str, str]]:
        """
        获取完整对话历史
        
        返回:
            消息历史列表
        """
        return self.messages.copy()