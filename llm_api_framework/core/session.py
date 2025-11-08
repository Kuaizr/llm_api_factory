from typing import Dict, List, Generator, Any, Optional, Union, Callable, AsyncGenerator
from .conversation import ConversationManager
from .router import APIClientRouter
from .executor import APIExecutor
from ..utils.config_manager import ConfigManager
import base64
from io import BytesIO
from PIL import Image
import json

class LLMSession:
    """统一的高级API会话接口"""
    def __init__(self, config_path: str = "configs/default.json"):
        cfg = ConfigManager(config_path)
        self.conversation = ConversationManager(
            max_context_tokens=cfg.get_context_max_tokens(),
            overflow_strategy=cfg.get_context_overflow_strategy(),
            reserve_recent_n=cfg.get_context_reserve_recent_n(),
            tokenizer_model=cfg.get_context_tokenizer_model(),
        )
        self.router = APIClientRouter(config_path)
        self.executor = APIExecutor(self.router, self.conversation)
        # 注入基于当前模型的自动总结器（在会话超限且策略为 summarize 时使用）
        self.conversation.set_summarizer(self._build_model_summarizer())
        
        if system_prompt := cfg.get_system_prompt():
            self.conversation.add_message({"role": "system", "content": system_prompt})
    
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
        
        self.conversation.add_message({"role": role, "content": contents})
    
    def add_message(self, message: Union[Dict[str, Any], str], content: Any = None, **kwargs):
        """添加消息到对话历史"""
        if isinstance(message, dict):
            payload = message
        else:
            payload = {"role": message}
            if content is not None:
                payload["content"] = content
            if kwargs:
                payload.update(kwargs)
        self.conversation.add_message(payload)
    
    def clear_messages(self, keep_system_prompt: bool = True):
        """清除对话历史"""
        self.conversation.clear_messages(keep_system_prompt)
    
    def get_conversation_history(self) -> List[Dict[str, Any]]:
        """获取完整对话历史"""
        return self.conversation.get_history()

    def _pil_to_base64(self, image) -> str:
        """将PIL.Image转换为base64字符串"""
        buffered = BytesIO()
        image.save(buffered, format="JPEG")
        return base64.b64encode(buffered.getvalue()).decode('utf-8')

    def _build_model_summarizer(self) -> Callable[[List[Dict[str, Any]]], Dict[str, Any]]:
        def _summarizer(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
            client = self.router.get_current_client()
            prompt_system = (
                "你是对话总结助手。请将提供的历史消息总结为一个简洁的系统提示，"
                "用中文，保留关键事实、用户意图、重要约束或中间结论，避免重复与赘述。"
                "仅输出总结文本，不要包含前后缀或解释。"
            )
            compact_history = self._serialize_messages(messages)
            summarization_messages = [
                {"role": "system", "content": prompt_system},
                {"role": "user", "content": f"请总结以下历史消息：\n{compact_history}"}
            ]
            try:
                result = client.call_api(summarization_messages)
                if isinstance(result, dict):
                    content = result.get("content")
                    if content:
                        return {"role": "system", "content": content}
            except Exception:
                pass
            # 兜底：粗略拼接为一条系统摘要
            return {"role": "system", "content": compact_history[:2000]}
        return _summarizer

    def _serialize_messages(self, messages: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content")
            if isinstance(content, list):
                # 视觉/多模态等复杂内容用占位提示，避免爆量
                text = "[多模态内容省略]"
            else:
                try:
                    if isinstance(content, (dict, list)):
                        text = json.dumps(content, ensure_ascii=False)
                    else:
                        text = str(content) if content is not None else ""
                except Exception:
                    text = str(content)
            lines.append(f"{role}: {text}")
        return "\n".join(lines)

    def call_api(self, prompt: str, tools: Optional[List[Dict[str, Any]]] = None, **kwargs) -> Dict[str, Any]:
        """同步API调用"""
        response = self.executor.call_api(prompt, tools=tools, **kwargs)
        if isinstance(response, dict) and not response.get("error"):
            self.conversation.add_message(response)
        return response

    async def call_api_async(self, prompt: str, tools: Optional[List[Dict[str, Any]]] = None, **kwargs) -> Dict[str, Any]:
        """异步API调用"""
        response = await self.executor.call_api_async(prompt, tools=tools, **kwargs)
        if isinstance(response, dict) and not response.get("error"):
            self.conversation.add_message(response)
        return response

    def stream_api(self, prompt: str, **kwargs) -> Generator[Dict[str, Any], None, None]:
        """流式API调用"""
        return self.executor.stream_api(prompt, **kwargs)

    async def stream_api_async(self, prompt: str, **kwargs) -> AsyncGenerator[Dict[str, Any], None]:
        """异步流式API调用"""
        async for chunk in self.executor.stream_api_async(prompt, **kwargs):
            yield chunk

    def stream_api(self, prompt: str, **kwargs) -> Generator[Dict[str, Any], None, None]:
        """流式API调用"""
        return self.executor.stream_api(prompt, **kwargs)