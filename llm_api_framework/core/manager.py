from typing import Dict, Any, Generator, AsyncGenerator, List, Optional, Union
from ..utils.config_manager import ConfigManager
from ..utils.logger import Logger
from ..utils.monitor import Monitor
from .factory import PlatformFactory
from .client import APIClient
import time
import os
import base64
from PIL import Image
from io import BytesIO
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

    def add_vision_message(self, role: str, images: list, detail: str = "low"):
        """
        添加视觉消息到对话历史

        参数:
            role: 消息角色 (user/assistant/system)
            images: 图片列表(URL或本地路径)
            detail: 图片处理细节 (low/high)
        """
        # 判断当前模型是否支持视觉输入
        if not self.config.get_vision():
            raise NotImplementedError("Current model does not support vision input")
        
        contents = []
        for img in images:
            if isinstance(img, str):
                # 处理URL
                if img.startswith(("http://", "https://")):
                    contents.append({
                        "type": "image_url",
                        "image_url": {"url": img, "detail": detail}
                    })
                else:
                    # 处理本地文件
                    if not os.path.exists(img):
                        raise FileNotFoundError(f"Image file not found: {img}")
                    
                    # 打开图片并转换为base64
                    with Image.open(img) as image:
                        buffered = BytesIO()
                        image.save(buffered, format="JPEG")
                        image_base64_data = base64.b64encode(buffered.getvalue()).decode('utf-8')
                    
                    contents.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64_data}",
                            "detail": detail
                        }
                    })
            elif isinstance(img, Image.Image):  # 检查是否是PIL.Image对象
                
                buffered = BytesIO()
                img.save(buffered, format="JPEG")
                image_base64_data = base64.b64encode(buffered.getvalue()).decode('utf-8')
                
                contents.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64_data}",
                        "detail": detail
                    }
                })
            else:
                raise ValueError("Unsupported image type, must be URL, file path or PIL.Image")
        
        # 添加视觉消息到对话历史
        self.messages.append({
            "role": role,
            "content": contents
        })

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
            raw_response = client.call_api(self.messages, **kwargs)
            latency = time.time() - start_time
            self.monitor.record_latency(client, latency)
            
            response = {"error": None, "reasoning_content": None, "content": None}
            
            if "error" in raw_response:
                response["error"] = raw_response["error"]
                self.monitor.record_failure(client)
                return response
                
            # 统一响应格式
            if "choices" in raw_response and len(raw_response["choices"]) > 0:
                message = raw_response["choices"][0]["message"]
                response["reasoning_content"] = message.get("reasoning_content")
                response["content"] = message.get("content")
                
                if response["content"]:
                    self.add_message("assistant", response["content"])
                    self.monitor.record_success(client)
            elif "content" in raw_response:
                response["content"] = raw_response["content"]
                self.add_message("assistant", response["content"])
                self.monitor.record_success(client)
                
            return response
            
        except Exception as e:
            self.monitor.record_failure(client)
            self.logger.log_error(e)
            return {"error": str(e)}

    async def call_api_async(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """异步API调用，返回统一格式字典"""
        if not self.clients:
            return {"error": "No available API clients", "reasoning_content": None, "content": None}
            
        self.add_message("user", prompt)
        client = self.clients[self._current_client_index]
        
        try:
            start_time = time.time()
            if hasattr(client, 'call_api_async'):
                raw_response = await client.call_api_async(self.messages, **kwargs)
            else:
                raw_response = client.call_api(self.messages, **kwargs)
                
            latency = time.time() - start_time
            self.monitor.record_latency(client, latency)
            
            response = {"error": None, "reasoning_content": None, "content": None}
            
            if "error" in raw_response:
                response["error"] = raw_response["error"]
                self.monitor.record_failure(client)
                return response
                
            # 统一响应格式
            if "choices" in raw_response and len(raw_response["choices"]) > 0:
                message = raw_response["choices"][0]["message"]
                response["reasoning_content"] = message.get("reasoning_content")
                response["content"] = message.get("content")
                
                if response["content"]:
                    self.add_message("assistant", response["content"])
                    self.monitor.record_success(client)
            elif "content" in raw_response:
                response["content"] = raw_response["content"]
                self.add_message("assistant", response["content"])
                self.monitor.record_success(client)
                
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
                if "error" in chunk:
                    yield {"error": chunk["error"], "reasoning_content": None, "content": None}
                    return
                    
                response = {"error": None, "reasoning_content": None, "content": None}
                
                if "reasoning_content" in chunk:
                    response["reasoning_content"] = chunk["reasoning_content"]
                elif "content" in chunk:
                    full_response += chunk["content"]
                    response["content"] = chunk["content"]
                    
                yield response
                    
            self.add_message("assistant", full_response)
            
        except Exception as e:
            yield {"error": str(e)}

    async def stream_api_async(self, prompt: str, **kwargs) -> AsyncGenerator[Dict[str, Any], None]:
        """异步流式API调用，返回统一格式字典"""
        if not self.clients:
            yield {"error": "No available API clients", "reasoning_content": None, "content": None}
            return
            
        self.add_message("user", prompt)
        client = self.clients[self._current_client_index]
        full_response = ""
        start_time = time.time()
        
        try:
            if hasattr(client, 'stream_api_async'):
                stream = client.stream_api_async(self.messages, **kwargs)
            else:
                stream = client.stream_api(self.messages, **kwargs)
                
            async for chunk in stream:
                if "error" in chunk:
                    self.monitor.record_failure(client)
                    yield {"error": chunk["error"], "reasoning_content": None, "content": None}
                    return
                    
                response = {"error": None, "reasoning_content": None, "content": None}
                
                if "reasoning_content" in chunk:
                    response["reasoning_content"] = chunk["reasoning_content"]
                elif "content" in chunk:
                    full_response += chunk["content"]
                    response["content"] = chunk["content"]
                    
                yield response
                
            latency = time.time() - start_time
            self.monitor.record_latency(client, latency)
            if full_response:
                self.add_message("assistant", full_response)
                self.monitor.record_success(client)
            
        except Exception as e:
            yield {"error": str(e)}

    def get_conversation_history(self) -> List[Dict[str, str]]:
        """
        获取完整对话历史
        
        返回:
            消息历史列表
        """
        return self.messages.copy()