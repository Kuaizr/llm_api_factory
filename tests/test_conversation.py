import unittest
from typing import Any, Dict, List

from llm_api_framework.core.conversation import ConversationManager


class ConversationTests(unittest.TestCase):
    def test_add_tool_response(self):
        cm = ConversationManager()
        cm.add_tool_response("tool-call-1", "ok")
        self.assertEqual(cm.get_history()[0]["role"], "tool")
        self.assertEqual(cm.get_history()[0]["tool_call_id"], "tool-call-1")
        self.assertEqual(cm.get_history()[0]["content"], "ok")

    def test_trim_strategy_keeps_system_and_trims_to_latest(self):
        cm = ConversationManager(max_context_tokens=25, overflow_strategy="trim")
        # force predictable token counting
        cm._count_tokens = lambda msgs: len(msgs) * 10  # type: ignore
        cm.add_message({"role": "system", "content": "s"})
        cm.add_message({"role": "user", "content": "u1"})
        cm.add_message({"role": "assistant", "content": "a1"})
        # Now adding a 4th will exceed (each add enforces); final should keep latest only with system
        cm.add_message({"role": "user", "content": "u2"})
        history = cm.get_history()
        self.assertEqual(history[0]["role"], "system")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[-1]["content"], "u2")

    def test_summarize_strategy_reserves_recent_and_inserts_summary(self):
        cm = ConversationManager(
            max_context_tokens=30,
            overflow_strategy="summarize",
            reserve_recent_n=1,
        )
        cm._count_tokens = lambda msgs: len(msgs) * 10  # type: ignore
        # summarizer returns a system summary message
        cm.set_summarizer(lambda msgs: {"role": "system", "content": f"summary:{len(msgs)}"})
        cm.add_message({"role": "system", "content": "s"})
        cm.add_message({"role": "user", "content": "u1"})
        cm.add_message({"role": "assistant", "content": "a1"})
        # Add one more to exceed and trigger summarize: keep system + summary + last 1 (u2)
        cm.add_message({"role": "user", "content": "u2"})
        history = cm.get_history()
        self.assertEqual(history[0]["role"], "system")
        self.assertEqual(history[1]["role"], "system")  # summary
        self.assertTrue(str(history[1]["content"]).startswith("summary:"))
        # recent one should be u2
        self.assertEqual(history[-1]["content"], "u2")


if __name__ == "__main__":
    unittest.main()

