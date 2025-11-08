import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from llm_api_framework.core.session import LLMSession
from llm_api_framework.core.conversation import ConversationManager


class _FakeConfigManager:
    def __init__(self, _path: Optional[str] = None):
        pass

    def get_system_prompt(self) -> Optional[str]:
        return "系统提示"

    def get_context_max_tokens(self) -> int:
        return 128

    def get_context_overflow_strategy(self) -> str:
        return "summarize"

    def get_context_reserve_recent_n(self) -> int:
        return 2

    def get_context_tokenizer_model(self) -> str:
        return "gpt-4o-mini"


class _FakeRouter:
    def __init__(self, _config_path: str):
        self.config = type("cfg", (), {"get_max_retries": lambda self: 3})()

    def get_current_client(self):
        class _Client:
            def call_api(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
                return {"role": "assistant", "content": "summary-ok"}

        return _Client()


class _FakeExecutor:
    def __init__(self, _router, conversation: ConversationManager):
        self.conversation = conversation
        self.last_call = None

    def call_api(self, prompt: str, tools: Optional[List[Dict[str, Any]]] = None, **kwargs) -> Dict[str, Any]:
        self.last_call = {"prompt": prompt, "tools": tools, "kwargs": kwargs}
        self.conversation.add_message({"role": "user", "content": prompt})
        return {"role": "assistant", "content": "ok"}


class SessionTests(unittest.TestCase):
    def test_session_init_with_context_from_config_and_message_append(self):
        with patch("llm_api_framework.core.session.ConfigManager", new=_FakeConfigManager), patch(
            "llm_api_framework.core.session.APIClientRouter", new=_FakeRouter
        ), patch("llm_api_framework.core.session.APIExecutor", new=_FakeExecutor):
            s = LLMSession("dummy.json")
            self.assertEqual(s.conversation.max_context_tokens, 128)
            self.assertEqual(s.conversation.overflow_strategy, "summarize")
            self.assertEqual(s.conversation.reserve_recent_n, 2)
            # 系统提示已加入
            self.assertEqual(s.get_conversation_history()[0]["role"], "system")
            # 调用后，assistant 消息会被加入历史
            msg = s.call_api("hi")
            self.assertEqual(msg["content"], "ok")
            self.assertEqual(s.get_conversation_history()[-1]["role"], "assistant")


if __name__ == "__main__":
    unittest.main()

