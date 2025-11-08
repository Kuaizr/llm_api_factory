import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch
import importlib
import sys
from types import ModuleType


class _RespObj:
    def __init__(self, payload: Dict[str, Any]):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _ChoiceMsg:
    def __init__(self, message: Dict[str, Any]):
        self.message = _ModelMsg(message)


class _ModelMsg:
    def __init__(self, data: Dict[str, Any]):
        self._data = data

    def model_dump(self) -> Dict[str, Any]:
        return self._data


class ClientsTests(unittest.TestCase):
    def test_free_aitools_returns_message(self):
        # 预注入最小 core 模块以避免循环导入
        core_pkg = ModuleType("llm_api_framework.core")
        core_client = ModuleType("llm_api_framework.core.client")
        core_error_types = ModuleType("llm_api_framework.core.error_types")
        class _Err:  # minimal ErrorType stub
            NETWORK = "NETWORK"
        core_error_types.ErrorType = _Err
        sys.modules["llm_api_framework.core"] = core_pkg
        sys.modules["llm_api_framework.core.client"] = core_client
        sys.modules["llm_api_framework.core.error_types"] = core_error_types
        mod = importlib.import_module("llm_api_framework.clients.free_aitools_client")
        FreeAitoolsClient = mod.FreeAitoolsClient
        client = FreeAitoolsClient(api_key="x", model="m", base_url="http://mock")
        payload = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        with patch("llm_api_framework.clients.base_requests_client.requests.post", new=lambda *a, **k: _RespObj(payload)):
            msg = client.call_api([{"role": "user", "content": "hi"}])
            self.assertEqual(msg["role"], "assistant")
            self.assertEqual(msg["content"], "ok")

    def test_infini_returns_message(self):
        core_pkg = ModuleType("llm_api_framework.core")
        core_client = ModuleType("llm_api_framework.core.client")
        core_error_types = ModuleType("llm_api_framework.core.error_types")
        class _Err:
            NETWORK = "NETWORK"
        core_error_types.ErrorType = _Err
        sys.modules["llm_api_framework.core"] = core_pkg
        sys.modules["llm_api_framework.core.client"] = core_client
        sys.modules["llm_api_framework.core.error_types"] = core_error_types
        mod = importlib.import_module("llm_api_framework.clients.infini_client")
        InfiniClient = mod.InfiniClient
        client = InfiniClient(api_key="x", model="m", base_url="http://mock")
        payload = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        with patch("llm_api_framework.clients.base_requests_client.requests.post", new=lambda *a, **k: _RespObj(payload)):
            msg = client.call_api([{"role": "user", "content": "hi"}])
            self.assertEqual(msg["role"], "assistant")
            self.assertEqual(msg["content"], "ok")

    def test_siliconflow_returns_message(self):
        core_pkg = ModuleType("llm_api_framework.core")
        core_client = ModuleType("llm_api_framework.core.client")
        core_error_types = ModuleType("llm_api_framework.core.error_types")
        class _Err:
            NETWORK = "NETWORK"
        core_error_types.ErrorType = _Err
        sys.modules["llm_api_framework.core"] = core_pkg
        sys.modules["llm_api_framework.core.client"] = core_client
        sys.modules["llm_api_framework.core.error_types"] = core_error_types
        mod = importlib.import_module("llm_api_framework.clients.siliconflow_client")
        SiliconFlowClient = mod.SiliconFlowClient
        client = SiliconFlowClient(api_key="x", model="m", base_url="http://mock")
        payload = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        with patch("llm_api_framework.clients.base_requests_client.requests.post", new=lambda *a, **k: _RespObj(payload)):
            msg = client.call_api([{"role": "user", "content": "hi"}])
            self.assertEqual(msg["role"], "assistant")
            self.assertEqual(msg["content"], "ok")

    def test_moda_returns_message(self):
        core_pkg = ModuleType("llm_api_framework.core")
        core_client = ModuleType("llm_api_framework.core.client")
        core_error_types = ModuleType("llm_api_framework.core.error_types")
        class _Err:
            NETWORK = "NETWORK"
        core_error_types.ErrorType = _Err
        sys.modules["llm_api_framework.core"] = core_pkg
        sys.modules["llm_api_framework.core.client"] = core_client
        sys.modules["llm_api_framework.core.error_types"] = core_error_types
        mod = importlib.import_module("llm_api_framework.clients.moda_client")
        MoDaClient = mod.MoDaClient
        class _SyncClient:
            class _Chat:
                class _Completions:
                    def create(self, **kwargs):
                        return type("R", (), {"choices": [_ChoiceMsg({"role":"assistant","content":"ok"})]})
                completions = _Completions()
            chat = _Chat()
        with patch("llm_api_framework.clients.base_openai_client.openai.OpenAI", new=lambda *a, **k: _SyncClient()):
            client = MoDaClient(api_key="x", model="m", base_url="http://mock")
            msg = client.call_api([{"role": "user", "content": "hi"}])
            self.assertEqual(msg["role"], "assistant")
            self.assertEqual(msg["content"], "ok")

    def test_openai_returns_message(self):
        core_pkg = ModuleType("llm_api_framework.core")
        core_client = ModuleType("llm_api_framework.core.client")
        core_error_types = ModuleType("llm_api_framework.core.error_types")
        class _Err:
            NETWORK = "NETWORK"
        core_error_types.ErrorType = _Err
        sys.modules["llm_api_framework.core"] = core_pkg
        sys.modules["llm_api_framework.core.client"] = core_client
        sys.modules["llm_api_framework.core.error_types"] = core_error_types
        mod = importlib.import_module("llm_api_framework.clients.openai_client")
        OpenAIClient = mod.OpenAIClient
        # 提供一个具体实现以绕过抽象方法限制
        class _ConcreteOpenAI(OpenAIClient):
            async def call_api_async(self, messages, **kwargs):
                return {}
            async def stream_api_async(self, messages, **kwargs):
                if False:
                    yield {}
        class _SyncClient:
            class _Chat:
                class _Completions:
                    def create(self, **kwargs):
                        return type("R", (), {"choices": [_ChoiceMsg({"role":"assistant","content":"ok"})]})
                completions = _Completions()
            chat = _Chat()
        with patch("llm_api_framework.clients.base_openai_client.openai.OpenAI", new=lambda *a, **k: _SyncClient()):
            client = _ConcreteOpenAI(api_key="x", base_url="http://mock")
            msg = client.call_api([{"role": "user", "content": "hi"}])
            self.assertEqual(msg.get("content"), "ok")


if __name__ == "__main__":
    unittest.main()

