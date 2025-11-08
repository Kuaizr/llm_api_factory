import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from llm_api_framework.core.conversation import ConversationManager
from llm_api_framework.core.executor import APIExecutor
from llm_api_framework.core.error_types import Action, ErrorType
from llm_api_framework.core.session import LLMSession


class _StubConfig:
    def get_max_retries(self) -> int:
        return 1

    def get_error_policy(self, _error_type: ErrorType) -> Action:
        return Action.ABORT


class _StubClient:
    def __init__(self, response: Dict[str, Any]):
        self._response = response
        self.calls: List[Dict[str, Any]] = []

    def call_api(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return self._response

    def handle_error(self, _response: Dict[str, Any]) -> ErrorType:
        return ErrorType.OTHER


class _StubRouter:
    def __init__(self, client: _StubClient):
        self._client = client
        self.config = _StubConfig()
        self.switch_count = 0

    def get_current_client(self) -> _StubClient:
        return self._client
    
    def select_client_for_request(self) -> _StubClient:
        return self._client

    def process_error(self, error_type: ErrorType) -> Action:
        return self.config.get_error_policy(error_type)

    def switch_client(self) -> None:
        self.switch_count += 1


class _FakeConfigManager:
    def __init__(self, _config_path: Optional[str] = None):
        pass

    def get_system_prompt(self) -> Optional[str]:
        return None
    # 新增上下文相关默认值，兼容 LLMSession 初始化
    def get_context_max_tokens(self) -> int:
        return 100000
    def get_context_overflow_strategy(self) -> str:
        return "trim"
    def get_context_reserve_recent_n(self) -> int:
        return 1
    def get_context_tokenizer_model(self) -> str:
        return "gpt-4o-mini"


class _FakeRouter:
    def __init__(self, _config_path: Optional[str] = None):
        self.config = _StubConfig()


class _FakeExecutor:
    def __init__(self, _router: _FakeRouter, conversation: ConversationManager):
        self.conversation = conversation
        self.last_call: Optional[Dict[str, Any]] = None
        self.response: Dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "lookup_time", "arguments": '{"city": "Beijing"}'},
                }
            ],
        }

    def call_api(self, prompt: str, tools: Optional[List[Dict[str, Any]]] = None, **kwargs: Any) -> Dict[str, Any]:
        self.last_call = {"prompt": prompt, "tools": tools, "kwargs": kwargs}
        self.conversation.add_message({"role": "user", "content": prompt})
        return self.response


class ToolCallingTests(unittest.TestCase):
    def test_executor_returns_message_with_tool_calls(self) -> None:
        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup_weather", "arguments": '{"city": "Shanghai"}'},
            }
        ]
        response_message = {"role": "assistant", "content": None, "tool_calls": tool_calls}
        stub_client = _StubClient(response_message)
        stub_router = _StubRouter(stub_client)
        conversation = ConversationManager()
        executor = APIExecutor(stub_router, conversation)

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "lookup_weather",
                    "description": "Get weather info",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        result = executor.call_api("请查询上海的天气", tools=tools, tool_choice={"type": "function"})

        self.assertEqual(result, response_message)
        history = conversation.get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "请查询上海的天气")
        self.assertTrue(stub_client.calls, "call_api 未被触发")
        self.assertEqual(stub_client.calls[0]["kwargs"]["tools"], tools)
        self.assertEqual(stub_client.calls[0]["kwargs"]["tool_choice"], {"type": "function"})

    def test_session_adds_tool_call_message(self) -> None:
        with patch("llm_api_framework.core.session.ConfigManager", new=_FakeConfigManager), patch(
            "llm_api_framework.core.session.APIClientRouter", new=_FakeRouter
        ), patch("llm_api_framework.core.session.APIExecutor", new=_FakeExecutor):
            session = LLMSession(config_path="dummy.json")
            tools = [
                {
                    "type": "function",
                    "function": {"name": "lookup_time", "description": "Get local time", "parameters": {}},
                }
            ]
            tool_choice = {"type": "function", "function": {"name": "lookup_time"}}

            message = session.call_api("现在几点？", tools=tools, tool_choice=tool_choice)

            self.assertEqual(message["tool_calls"][0]["function"]["name"], "lookup_time")
            history = session.get_conversation_history()
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["role"], "user")
            self.assertEqual(history[0]["content"], "现在几点？")
            self.assertEqual(history[1], message)
            self.assertEqual(session.executor.last_call["tools"], tools)
            self.assertEqual(session.executor.last_call["kwargs"]["tool_choice"], tool_choice)


if __name__ == "__main__":
    unittest.main()

