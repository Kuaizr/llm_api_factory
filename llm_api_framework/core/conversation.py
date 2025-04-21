from typing import Dict, List

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