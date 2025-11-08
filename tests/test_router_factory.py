import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from llm_api_framework.core.router import APIClientRouter


class _FakeConfig:
    def __init__(self):
        self._cfg = {
            "platform_order": ["openai", "siliconflow"],
            "platforms": {
                "openai": {"base_url": "x", "model": "m1", "api_keys": ["k1", "k2"]},
                "siliconflow": {"base_url": "y", "model": "m2", "api_keys": ["k3"]},
            },
            "max_retries": 5,
        }

    def load_config(self) -> Dict[str, Any]:
        return self._cfg

    def get_platform_order(self) -> List[str]:
        return self._cfg["platform_order"]

    def get_platform_config(self, platform: str) -> Dict[str, Any]:
        return self._cfg["platforms"][platform]

    def get_api_keys(self, platform: str) -> List[str]:
        return self._cfg["platforms"][platform]["api_keys"]

    def get_model_name(self, platform: str) -> str:
        return self._cfg["platforms"][platform]["model"]

    def get_error_policy(self, _t):
        return None

    def get_max_retries(self) -> int:
        return self._cfg["max_retries"]
    
    def get_routing_strategy(self) -> str:
        routing = self._cfg.get("routing", {})
        return routing.get("strategy", "failover")


class _FakeClient:
    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url


class RouterFactoryTests(unittest.TestCase):
    def test_initialize_clients_and_switch(self):
        with patch("llm_api_framework.core.router.ConfigManager", new=lambda path: _FakeConfig()), patch(
            "llm_api_framework.core.factory.PlatformFactory.create_client",
            new=lambda platform, api_key, model, base_url=None: _FakeClient(api_key, model, base_url),
        ):
            r = APIClientRouter("dummy.json")
            # total clients = 2 (openai) + 1 (siliconflow) = 3
            self.assertEqual(len(r.clients), 3)
            first = r.get_current_client()
            r.switch_client()
            self.assertIsNot(first, r.get_current_client())
            # get_max_retries forwarded
            self.assertEqual(r.get_max_retries(), 5)

    def test_round_robin_strategy_selection(self):
        cfg = _FakeConfig()
        def _cfg_with_rr():
            c = _FakeConfig()
            c._cfg["routing"] = {"strategy": "round_robin"}
            return c
        with patch("llm_api_framework.core.router.ConfigManager", new=lambda path: _cfg_with_rr()), patch(
            "llm_api_framework.core.factory.PlatformFactory.create_client",
            new=lambda platform, api_key, model, base_url=None: _FakeClient(api_key, model, base_url),
        ):
            r = APIClientRouter("dummy.json")
            a = r.select_client_for_request()
            b = r.select_client_for_request()
            c = r.select_client_for_request()
            # 3 clients, round-robin should rotate and wrap
            self.assertNotEqual(a, b)
            self.assertNotEqual(b, c)
            self.assertNotEqual(c, a)


if __name__ == "__main__":
    unittest.main()

