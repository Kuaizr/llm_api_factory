from typing import Dict, Any, Generator, AsyncGenerator
import time
from .client import APIClient
from ..utils.monitor import Monitor
from .error_types import ErrorType

class APIExecutor:
    """执行API调用并处理结果"""
    def __init__(self, router, conversation):
        self.router = router
        self.conversation = conversation
        self.monitor = Monitor()
    
    def stream_api(self, prompt: str, **kwargs) -> Generator[Dict[str, Any], None, None]:
        """执行流式API调用"""
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
                
                if response["content"]:
                    self.conversation.add_message("assistant", response["content"])
                    self.monitor.record_success(client)
                    return {"error": None, "content": response["content"], "reasoning_content": response["reasoning_content"]}
                
            except Exception as e:
                self.monitor.record_failure(client)
                from ..utils.logger import Logger
                Logger().log_error(e)
                return {"error": str(e), "content": None, "reasoning_content": None}
        
        return {"error": "Max retries exceeded", "content": None, "reasoning_content": None}