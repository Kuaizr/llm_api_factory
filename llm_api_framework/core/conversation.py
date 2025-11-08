from typing import Dict, List, Any, Callable, Optional
import json
try:
    import tiktoken  # type: ignore
except Exception:
    tiktoken = None

class ConversationManager:
    """管理对话上下文"""
    def __init__(
        self,
        max_context_tokens: int = 100000,
        overflow_strategy: str = "trim",
        reserve_recent_n: int = 1,
        tokenizer_model: str = "gpt-4o-mini",
        summarizer: Optional[Callable[[List[Dict[str, Any]]], Dict[str, Any]]] = None,
    ):
        self.messages: List[Dict[str, Any]] = []
        self.max_context_tokens = max_context_tokens
        self.overflow_strategy = overflow_strategy  # "trim" | "summarize"
        self.reserve_recent_n = max(0, reserve_recent_n)
        self.tokenizer_model = tokenizer_model
        self._summarizer = summarizer
        self._encoding = None
        if tiktoken:
            try:
                self._encoding = tiktoken.encoding_for_model(self.tokenizer_model)
            except Exception:
                try:
                    self._encoding = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    self._encoding = None
    
    def add_message(self, message: Dict[str, Any]):
        self.messages.append(message)
        self._enforce_budget()
    
    def add_tool_response(self, tool_call_id: str, content: Any):
        self.add_message({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content
        })
    
    def clear_messages(self, keep_system_prompt: bool = True):
        if keep_system_prompt and len(self.messages) > 0 and self.messages[0].get("role") == "system":
            self.messages = [self.messages[0]]
        else:
            self.messages = []
    
    def get_history(self) -> List[Dict[str, Any]]:
        return self.messages.copy()

    def set_max_context_tokens(self, max_tokens: int):
        self.max_context_tokens = max_tokens
        self._enforce_budget()

    def set_overflow_strategy(self, strategy: str, reserve_recent_n: Optional[int] = None):
        if strategy not in ("trim", "summarize"):
            raise ValueError("strategy must be 'trim' or 'summarize'")
        self.overflow_strategy = strategy
        if reserve_recent_n is not None:
            self.reserve_recent_n = max(0, reserve_recent_n)
        self._enforce_budget()

    def set_summarizer(self, summarizer: Callable[[List[Dict[str, Any]]], Dict[str, Any]]):
        self._summarizer = summarizer

    def _count_tokens(self, messages: List[Dict[str, Any]]) -> int:
        if not messages:
            return 0
        if self._encoding is None:
            # Fallback: rough estimate ~ 4 chars per token
            text = "\n".join(
                f"{m.get('role','')}:{self._stringify(m.get('content'))}"
                for m in messages
            )
            return max(1, len(text) // 4)
        tokens = 0
        for m in messages:
            role = str(m.get("role", ""))
            content = self._stringify(m.get("content"))
            tokens += len(self._encoding.encode(role))
            tokens += len(self._encoding.encode(content))
            # Add a small per-message overhead
            tokens += 4
        return tokens

    def _stringify(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, (str, int, float, bool)):
            return str(content)
        try:
            return json.dumps(content, ensure_ascii=False)
        except Exception:
            return str(content)

    def _enforce_budget(self):
        total = self._count_tokens(self.messages)
        if total <= self.max_context_tokens:
            return

        if self.overflow_strategy == "summarize" and self._summarizer:
            self._summarize_then_trim_if_needed()
        else:
            self._trim_oldest_messages()

    def _trim_oldest_messages(self):
        if not self.messages:
            return
        # Keep system prompt at index 0 if present
        keep_start = 1 if len(self.messages) > 0 and self.messages[0].get("role") == "system" else 0
        while len(self.messages) > (keep_start + 1) and self._count_tokens(self.messages) > self.max_context_tokens:
            # Remove the second message (index keep_start)
            del self.messages[keep_start]

    def _summarize_then_trim_if_needed(self):
        if not self.messages:
            return
        has_system = len(self.messages) > 0 and self.messages[0].get("role") == "system"
        start_idx = 1 if has_system else 0
        if len(self.messages) - start_idx <= self.reserve_recent_n:
            # Not enough to summarize, fallback to trim
            self._trim_oldest_messages()
            return
        recent = self.messages[-self.reserve_recent_n:] if self.reserve_recent_n > 0 else []
        to_summarize = self.messages[start_idx: len(self.messages) - len(recent)]
        if to_summarize:
            try:
                summary_msg = self._summarizer(to_summarize)
                if not isinstance(summary_msg, dict) or "role" not in summary_msg:
                    # Fallback summary format
                    summary_msg = {"role": "system", "content": self._stringify(summary_msg)}
            except Exception:
                # On summarizer failure, fallback to trim
                self._trim_oldest_messages()
                return
            new_messages: List[Dict[str, Any]] = []
            if has_system:
                new_messages.append(self.messages[0])
            new_messages.append(summary_msg)
            new_messages.extend(recent)
            self.messages = new_messages

        # Ensure within budget; if still exceeds, trim
        if self._count_tokens(self.messages) > self.max_context_tokens:
            self._trim_oldest_messages()