import unittest
from typing import Any, Dict, List, Optional

from llm_api_framework.core.conversation import ConversationManager
from llm_api_framework.core.executor import APIExecutor
from llm_api_framework.core.error_types import Action, ErrorType


class _StubClient:
    def __init__(self, responses: List[Dict[str, Any]]):
        self.responses = responses
        self.calls = 0

    def call_api(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[idx]

    def handle_error(self, response: Dict[str, Any]) -> ErrorType:
        return ErrorType.RATE_LIMIT if response.get("error") else ErrorType.OTHER


class _StubRouter:
    def __init__(self, client: _StubClient, actions: List[Action], max_retries: int = 2):
        self._client = client
        self._actions = actions
        self._action_calls = 0
        self._max_retries = max_retries
        self.switches = 0

        class _Cfg:
            def __init__(self, v: int):
                self.v = v

            def get_max_retries(self) -> int:
                return self.v

        self.config = _Cfg(max_retries)

    def get_current_client(self) -> _StubClient:
        return self._client
    
    def select_client_for_request(self) -> _StubClient:
        return self._client

    def process_error(self, _error_type: ErrorType) -> Action:
        idx = min(self._action_calls, len(self._actions) - 1)
        self._action_calls += 1
        return self._actions[idx]

    def switch_client(self):
        self.switches += 1


class ExecutorTests(unittest.TestCase):
    def test_call_api_success_returns_message(self):
        client = _StubClient([{"role": "assistant", "content": "ok"}])
        router = _StubRouter(client, [Action.ABORT])
        cm = ConversationManager()
        ex = APIExecutor(router, cm)

        result = ex.call_api("hi")
        self.assertEqual(result, {"role": "assistant", "content": "ok"})
        # executor 不添加 assistant 到历史，只添加 user
        self.assertEqual(len(cm.get_history()), 1)
        self.assertEqual(cm.get_history()[0]["role"], "user")

    def test_call_api_error_abort(self):
        client = _StubClient([{"error": "rate limit"}])
        router = _StubRouter(client, [Action.ABORT])
        cm = ConversationManager()
        ex = APIExecutor(router, cm)

        result = ex.call_api("hi")
        self.assertIn("error", result)

    def test_call_api_error_retry_then_abort(self):
        client = _StubClient([{"error": "rate limit"}, {"error": "rate limit"}])
        router = _StubRouter(client, [Action.RETRY, Action.ABORT], max_retries=2)
        cm = ConversationManager()
        ex = APIExecutor(router, cm)

        result = ex.call_api("hi")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()

