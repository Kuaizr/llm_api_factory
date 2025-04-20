from typing import Dict, List, Any, Generator, AsyncGenerator, Optional
from .client import APIClient
from .factory import PlatformFactory
from ..utils.config_manager import ConfigManager
from ..utils.logger import Logger
from ..utils.monitor import Monitor
from .error_types import ErrorType, Action
import time
import asyncio

class ConversationManager:
    """管理对话上下文"""
    def __init__(self):
        self.messages: List[Dict[str, str]] = []
    
    def add_message(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})
    
    def clear_messages(self, keep_system_prompt: bool = True):
        if keep_system_prompt and len(self.messages) > 0 and self.messages[0]["role"] == "system":
            self.messages = [self.messages[0]]
        else:
            self.messages = []
    
    def get_history(self) -> List[Dict[str, str]]:
        return self.messages.copy()

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
        return self.clients[self._current_index]

    def process_error(self, error_type: ErrorType) -> Action:
        """根据错误类型返回处理动作"""
        policy = {
            ErrorType.NETWORK: Action.RETRY,
            ErrorType.RATE_LIMIT: Action.SWITCH,
            ErrorType.QUOTA_EXCEEDED: Action.SWITCH,
            ErrorType.AUTH_FAILURE: Action.ABORT,
            ErrorType.INVALID_REQUEST: Action.ABORT
        }
        return policy.get(error_type, Action.ABORT)
    
    def switch_client(self):
        """切换到下一个可用客户端"""
        self._current_index = (self._current_index + 1) % len(self.clients)

class APIExecutor:
    """执行API调用并处理结果"""
    def __init__(self, router: APIClientRouter, conversation: ConversationManager):
        self.router = router
        self.conversation = conversation
        self.monitor = Monitor()
    
    def stream_api(self, prompt: str, **kwargs) -> Generator[Dict[str, Any], None, None]:
        """执行流式API调用
        参数:
            prompt: 用户输入的提示文本
            **kwargs: 其他API参数
        返回:
            生成器，产生流式响应块
        """
        self.conversation.add_message("user", prompt)
        messages = self.conversation.get_history()
        max_retries = self.router.get_max_retries() or 3
        retry_count = 0
        full_response = ""
        
        while retry_count < max_retries:
            client = self.router.get_current_client()
            try:
                start_time = time.time()
                for chunk in client.stream_api(messages, **kwargs):
                    if "error" in chunk:
                        error_type = client.handle_error(chunk)
                        action = self.router.process_error(error_type)
                        
                        if action == Action.RETRY:
                            retry_count += 1
                            break
                        elif action == Action.SWITCH:
                            self.router.switch_client()
                            retry_count = 0
                            break
                        yield chunk
                        return
                    
                    # 规范化流式响应格式
                    response = {
                        "error": None,
                        "content": chunk.get("content"),
                        "reasoning_content": chunk.get("reasoning_content")
                    }
                    if response["content"]:
                        full_response += response["content"]
                    yield response
                
                latency = time.time() - start_time
                self.monitor.record_latency(client, latency)
                if full_response:
                    self.monitor.record_success(client)
                    self.conversation.add_message("assistant", full_response)
                    return {
                        "error": None,
                        "content": full_response,
                        "reasoning_content": None
                    }
                return {
                    "error": None,
                    "content": None,
                    "reasoning_content": None
                }
                
            except Exception as e:
                retry_count += 1
                if retry_count >= max_retries:
                    yield {
                        "error": str(e),
                        "content": None,
                        "reasoning_content": None
                    }

    def call_api(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """同步API调用"""
        self.conversation.add_message("user", prompt)
        client = self.router.get_current_client()
        max_retries = self.router.config.get_max_retries() or 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                start_time = time.time()
                response = client.call_api(self.conversation.get_history(), **kwargs)
                latency = time.time() - start_time
                
                self.monitor.record_latency(client, latency)
                
                # 规范化响应格式
                if not isinstance(response, dict):
                    response = {"error": "Invalid response format", "content": None, "reasoning_content": None}
                else:
                    response.setdefault("error", None)
                    response.setdefault("content",
                        response.get("content") or
                        response.get("choices", [{}])[0].get("message", {}).get("content")
                    )
                    response.setdefault("reasoning_content",
                        response.get("reasoning_content") or
                        response.get("choices", [{}])[0].get("message", {}).get("reasoning_content")
                    )
                
                if response["error"]:
                    error_type = client.handle_error(response)
                    action = self.router.process_error(error_type)
                    
                    if action == Action.RETRY:
                        retry_count += 1
                        continue
                    elif action == Action.SWITCH:
                        self.router.switch_client()
                        retry_count = 0
                        continue
                    
                    self.monitor.record_failure(client)
                    return {"error": response["error"], "content": None, "reasoning_content": None}
                
                # 处理成功响应
                if response["content"]:
                    self.conversation.add_message("assistant", response["content"])
                    self.monitor.record_success(client)
                    return {"error": None, "content": response["content"], "reasoning_content": response["reasoning_content"]}
                
            except Exception as e:
                self.monitor.record_failure(client)
                Logger().log_error(e)
                return {"error": str(e), "content": None, "reasoning_content": None}
        
        return {"error": "Max retries exceeded", "content": None, "reasoning_content": None}

class LLMSession:
    """统一的高级API会话接口"""
    def __init__(self, config_path: str = "configs/default.json"):
        self.conversation = ConversationManager()
        self.router = APIClientRouter(config_path)
        self.executor = APIExecutor(self.router, self.conversation)
        
        # 初始化系统提示
        if system_prompt := ConfigManager(config_path).get_system_prompt():
            self.conversation.add_message("system", system_prompt)
    
    def call_api(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """同步API调用"""
        return self.executor.call_api(prompt, **kwargs)
    
    def add_message(self, role: str, content: str):
        """添加消息到对话历史"""
        self.conversation.add_message(role, content)
    
    def clear_messages(self, keep_system_prompt: bool = True):
        """清除对话历史"""
        self.conversation.clear_messages(keep_system_prompt)
    
    def get_conversation_history(self) -> List[Dict[str, str]]:
        """获取完整对话历史"""
        return self.conversation.get_history()

    def add_vision_message(self, role: str, images: list, detail: str = "low"):
        """添加视觉消息到对话历史"""
        contents = []
        for img in images:
            if isinstance(img, str):
                if img.startswith(("http://", "https://")):
                    contents.append({
                        "type": "image_url",
                        "image_url": {"url": img, "detail": detail}
                    })
                else:
                    from PIL import Image
                    if not isinstance(img, Image.Image):
                        img = Image.open(img)
                    contents.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{self._pil_to_base64(img)}",
                            "detail": detail
                        }
                    })
            elif hasattr(img, "save"):  # PIL.Image
                contents.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{self._pil_to_base64(img)}",
                        "detail": detail
                    }
                })
        
        self.conversation.add_message(role, contents)

    def _pil_to_base64(self, image) -> str:
        """将PIL.Image转换为base64字符串"""
        import base64
        from io import BytesIO
        
        buffered = BytesIO()
        image.save(buffered, format="JPEG")
        return base64.b64encode(buffered.getvalue()).decode('utf-8')

    def stream_api(self, prompt: str, **kwargs) -> Generator[Dict[str, Any], None, None]:
        """流式API调用"""
        return self.executor.stream_api(prompt, **kwargs)