import unittest
from typing import Any, Dict
from unittest.mock import patch
from io import StringIO

import importlib
import sys
from types import ModuleType


class _FakeObserver:
    def __init__(self, *a, **k):
        self._alive = False
        self.scheduled = []

    def schedule(self, handler, path, recursive=False):
        self.scheduled.append((handler, path, recursive))

    def start(self):
        self._alive = True

    def is_alive(self):
        return self._alive

    def stop(self):
        self._alive = False

    def join(self):
        pass


class ConfigManagerTests(unittest.TestCase):
    def test_context_readers(self):
        cfg = {
            "context": {
                "max_tokens": 1234,
                "overflow_strategy": "summarize",
                "reserve_recent_n": 3,
                "tokenizer_model": "gpt-4o-mini",
            }
        }
        # 导入模块以获取类定义
        mod = importlib.import_module("llm_api_framework.utils.config_manager")
        ConfigManager = mod.ConfigManager
        cm = object.__new__(ConfigManager)  # bypass __init__
        cm._config = cfg  # type: ignore
        self.assertEqual(cm.get_context_max_tokens(), 1234)
        self.assertEqual(cm.get_context_overflow_strategy(), "summarize")
        self.assertEqual(cm.get_context_reserve_recent_n(), 3)
        self.assertEqual(cm.get_context_tokenizer_model(), "gpt-4o-mini")

    def test_singleton_and_no_double_start_watching(self):
        # Provide minimal file content
        file_json = '{"platform_order": [], "platforms": {}}'
        # 预注入最小 core 包防止循环导入
        core_pkg = ModuleType("llm_api_framework.core")
        core_error_types = ModuleType("llm_api_framework.core.error_types")
        class _Err: pass
        core_error_types.ErrorType = _Err
        sys.modules["llm_api_framework.core"] = core_pkg
        sys.modules["llm_api_framework.core.error_types"] = core_error_types
        with patch("llm_api_framework.utils.config_manager.Path.exists", new=lambda self: True), patch(
            "builtins.open", new=lambda *a, **k: StringIO(file_json)
        ), patch(
            "llm_api_framework.utils.config_manager.Observer", new=_FakeObserver
        ):
            mod = importlib.import_module("llm_api_framework.utils.config_manager")
            ConfigManager = mod.ConfigManager
            a = ConfigManager("dummy.json")
            b = ConfigManager("dummy.json")
            self.assertIs(a, b)
            # Re-init should not start a second observer
            self.assertTrue(a._observer.is_alive())  # type: ignore


if __name__ == "__main__":
    unittest.main()

