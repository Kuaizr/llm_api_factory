from typing import Dict, Any, Generator, AsyncGenerator, List, Optional
import time
from ..utils.monitor import Monitor
from .error_types import Action

class APIExecutor:
    """执行API调用并处理结果"""
    def __init__(self, router, conversation):
        self.router = router
        self.conversation = conversation
        self.monitor = Monitor()
    
    def stream_api(self, prompt: str, **kwargs) -> Generator[Dict[str, Any], None, None]:
        """执行流式API调用"""
        self.conversation.add_message({"role": "user", "content": prompt})
        messages = self.conversation.get_history()
        max_retries = self.router.get_max_retries() or 3
        retry_count = 0
        full_response = ""
        
        while retry_count < max_retries:
            client = self.router.select_client_for_request()
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
                    self.conversation.add_message({"role": "assistant", "content": full_response})
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

    def call_api(self, prompt: str, tools: Optional[List[Dict[str, Any]]] = None, **kwargs) -> Dict[str, Any]:
        """同步API调用"""
        user_message = {"role": "user", "content": prompt}
        self.conversation.add_message(user_message)
        max_retries = self.router.config.get_max_retries() or 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                client = self.router.select_client_for_request()
                start_time = time.time()
                call_kwargs = dict(kwargs)
                if tools is not None:
                    call_kwargs["tools"] = tools
                response = client.call_api(self.conversation.get_history(), **call_kwargs)
                latency = time.time() - start_time
                
                self.monitor.record_latency(client, latency)
                
                if not isinstance(response, dict):
                    response = {"error": "Invalid response format"}
                
                if response.get("error"):
                    error_type = client.handle_error(response)
                    action = self.router.process_error(error_type)
                    
                    if action == Action.RETRY:
                        self.monitor.record_failure(client)
                        retry_count += 1
                        continue
                    elif action == Action.SWITCH:
                        self.monitor.record_failure(client)
                        self.router.switch_client()
                        retry_count = 0
                        continue
                    
                    self.monitor.record_failure(client)
                    return {"error": response["error"]}
                
                if "role" not in response:
                    self.monitor.record_failure(client)
                    return {"error": "Invalid message object from client"}
                
                self.monitor.record_success(client)
                return response
                
            except Exception as e:
                client = self.router.get_current_client()
                self.monitor.record_failure(client)
                from ..utils.logger import Logger
                Logger().log_error(e)
                return {"error": str(e)}
        
        return {"error": "Max retries exceeded"}

    async def call_api_async(self, prompt: str, tools: Optional[List[Dict[str, Any]]] = None, **kwargs) -> Dict[str, Any]:
        """异步API调用"""
        self.conversation.add_message({"role": "user", "content": prompt})
        max_retries = self.router.config.get_max_retries() or 3
        retry_count = 0
        while retry_count < max_retries:
            try:
                client = self.router.select_client_for_request()
                start_time = time.time()
                call_kwargs = dict(kwargs)
                if tools is not None:
                    call_kwargs["tools"] = tools
                response = await client.call_api_async(self.conversation.get_history(), **call_kwargs)
                latency = time.time() - start_time
                self.monitor.record_latency(client, latency)
                if not isinstance(response, dict):
                    response = {"error": "Invalid response format"}
                if response.get("error"):
                    error_type = client.handle_error(response)
                    action = self.router.process_error(error_type)
                    if action == Action.RETRY:
                        self.monitor.record_failure(client)
                        retry_count += 1
                        continue
                    elif action == Action.SWITCH:
                        self.monitor.record_failure(client)
                        self.router.switch_client()
                        retry_count = 0
                        continue
                    self.monitor.record_failure(client)
                    return {"error": response["error"]}
                if "role" not in response:
                    self.monitor.record_failure(client)
                    return {"error": "Invalid message object from client"}
                self.monitor.record_success(client)
                return response
            except Exception as e:
                client = self.router.get_current_client()
                self.monitor.record_failure(client)
                from ..utils.logger import Logger
                Logger().log_error(e)
                return {"error": str(e)}
        return {"error": "Max retries exceeded"}

    async def stream_api_async(self, prompt: str, **kwargs) -> AsyncGenerator[Dict[str, Any], None]:
        """异步流式API调用"""
        self.conversation.add_message({"role": "user", "content": prompt})
        messages = self.conversation.get_history()
        max_retries = self.router.get_max_retries() or 3
        retry_count = 0
        full_response = ""
        while retry_count < max_retries:
            client = self.router.select_client_for_request()
            try:
                start_time = time.time()
                async for chunk in client.stream_api_async(messages, **kwargs):
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
                    response = {
                        "error": None,
                        "content": chunk.get("content"),
                        "reasoning_content": chunk.get("reasoning_content"),
                    }
                    if response["content"]:
                        full_response += response["content"]
                    yield response
                latency = time.time() - start_time
                self.monitor.record_latency(client, latency)
                if full_response:
                    self.monitor.record_success(client)
                    self.conversation.add_message({"role": "assistant", "content": full_response})
                    yield {"error": None, "content": full_response, "reasoning_content": None}
                    return
                yield {"error": None, "content": None, "reasoning_content": None}
                return
            except Exception as e:
                retry_count += 1
                if retry_count >= max_retries:
                    yield {"error": str(e), "content": None, "reasoning_content": None}