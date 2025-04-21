from typing import Dict, List, Generator, Any
from .conversation import ConversationManager
from .router import APIClientRouter
from .executor import APIExecutor
from ..utils.config_manager import ConfigManager
import base64
from io import BytesIO
from PIL import Image

class LLMSession:
    """统一的高级API会话接口"""
    def __init__(self, config_path: str = "configs/default.json"):
        self.conversation = ConversationManager()
        self.router = APIClientRouter(config_path)
        self.executor = APIExecutor(self.router, self.conversation)
        
        if system_prompt := ConfigManager(config_path).get_system_prompt():
            self.conversation.add_message("system", system_prompt)
    
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
        
        self.conversation.add_message(role, contents)
    
    def add_message(self, role: str, content: str):
        """添加消息到对话历史"""
        self.conversation.add_message(role, content)
    
    def clear_messages(self, keep_system_prompt: bool = True):
        """清除对话历史"""
        self.conversation.clear_messages(keep_system_prompt)
    
    def get_conversation_history(self) -> List[Dict[str, str]]:
        """获取完整对话历史"""
        return self.conversation.get_history()

    def _pil_to_base64(self, image) -> str:
        """将PIL.Image转换为base64字符串"""
        buffered = BytesIO()
        image.save(buffered, format="JPEG")
        return base64.b64encode(buffered.getvalue()).decode('utf-8')

    def call_api(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """同步API调用"""
        return self.executor.call_api(prompt, **kwargs)

    def stream_api(self, prompt: str, **kwargs) -> Generator[Dict[str, Any], None, None]:
        """流式API调用"""
        return self.executor.stream_api(prompt, **kwargs)