import unittest
import asyncio
from typing import Any, Dict, List, Optional, AsyncGenerator as _AsyncGen
from unittest.mock import patch

from llm_api_framework.core.conversation import ConversationManager
from llm_api_framework.core.executor import APIExecutor
from llm_api_framework.core.session import LLMSession


class _AsyncClient:
    def __init__(self, message: Dict[str, Any], chunks: List[Dict[str, Any]]):
        self._message = message
        self._chunks = chunks
        self.calls = 0

    async def call_api_async(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        self.calls += 1
        return self._message

    async def stream_api_async(self, messages: List[Dict[str, Any]], **kwargs) -> _AsyncGen[Dict[str, Any], None]:
        for c in self._chunks:
            yield c

    def handle_error(self, response):
        return None


class _AsyncRouter:
    def __init__(self, client: _AsyncClient):
        self._client = client
        self.config = type("cfg", (), {"get_max_retries": lambda self: 2})()

    def select_client_for_request(self):
        return self._client

    def get_max_retries(self):
        return 2

    def process_error(self, _):
        return None

    def switch_client(self):
        pass


class AsyncTests(unittest.TestCase):
    def test_executor_call_api_async(self):
        async def _run():
            msg = {"role": "assistant", "content": "ok"}
            client = _AsyncClient(msg, [])
            router = _AsyncRouter(client)
            cm = ConversationManager()
            ex = APIExecutor(router, cm)
            res = await ex.call_api_async("hi")
            self.assertEqual(res["content"], "ok")
        asyncio.run(_run())

    def test_executor_stream_api_async(self):
        async def _run():
            chunks = [{"content": "a"}, {"content": "b"}]
            client = _AsyncClient({"role": "assistant", "content": "ok"}, chunks)
            router = _AsyncRouter(client)
            cm = ConversationManager()
            ex = APIExecutor(router, cm)
            acc = ""
            async for ch in ex.stream_api_async("hi"):
                if ch.get("content"):
                    acc += ch["content"]
            self.assertIn("a", acc)
            self.assertIn("b", acc)
        asyncio.run(_run())

    def test_session_call_api_async(self):
        async def _run():
            with patch("llm_api_framework.core.session.ConfigManager", new=lambda *a, **k: type("C", (), {
                "get_context_max_tokens": lambda self: 100000,
                "get_context_overflow_strategy": lambda self: "trim",
                "get_context_reserve_recent_n": lambda self: 1,
                "get_context_tokenizer_model": lambda self: "gpt-4o-mini",
                "get_system_prompt": lambda self: None,
            })()), patch("llm_api_framework.core.session.APIClientRouter", new=lambda *a, **k: _AsyncRouter(_AsyncClient({"role":"assistant","content":"ok"}, []))), patch(
                "llm_api_framework.core.session.APIExecutor"
            ) as ex_mock:
                async def _call_api_async(prompt, tools=None, **kwargs):
                    return {"role": "assistant", "content": "ok"}
                ex_inst = type("E", (), {"call_api_async": staticmethod(_call_api_async)})()
                ex_mock.return_value = ex_inst  # type: ignore
                s = LLMSession("dummy.json")
                res = await s.call_api_async("hi")
                self.assertEqual(res["content"], "ok")
        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()

