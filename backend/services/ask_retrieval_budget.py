"""Shared, in-memory budgets for one Ask retrieval run.

The budget is deliberately process-local and side-effect free. It bounds model
rounds, tool calls, search/read/graph work, content characters, and wall time;
callers still own dispatch and evidence interpretation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from time import perf_counter
from typing import Callable, Optional


MAX_MODEL_CALLS = 9
MAX_TOOL_CALLS = 16
MAX_SEARCH_CALLS = 4
MAX_READ_CALLS = 5
MAX_GRAPH_CALLS = 6
MAX_READ_CHARS = 60_000
MAX_INDEX_CHARS = 16_000
MAX_ORCHESTRATION_MS = 180_000


@dataclass
class RetrievalBudget:
    max_model_calls: int = MAX_MODEL_CALLS
    max_tool_calls: int = MAX_TOOL_CALLS
    max_search_calls: int = MAX_SEARCH_CALLS
    max_read_calls: int = MAX_READ_CALLS
    max_graph_calls: int = MAX_GRAPH_CALLS
    max_read_chars: int = MAX_READ_CHARS
    max_index_chars: int = MAX_INDEX_CHARS
    max_elapsed_ms: int = MAX_ORCHESTRATION_MS
    clock: Callable[[], float] = field(default=perf_counter, repr=False)
    model_calls: int = 0
    tool_calls: int = 0
    search_calls: int = 0
    read_calls: int = 0
    graph_calls: int = 0
    read_chars: int = 0
    index_chars: int = 0
    model_duration_ms: int = 0
    tool_duration_ms: int = 0
    blocked_calls: int = 0
    blocked_reasons: list[str] = field(default_factory=list)
    started_at: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.started_at = self.clock()

    def elapsed_ms(self) -> int:
        return max(0, int((self.clock() - self.started_at) * 1000))

    def remaining_read_chars(self) -> int:
        return max(0, self.max_read_chars - self.read_chars)

    def remaining_timeout_s(self) -> int:
        remaining_ms = max(0, self.max_elapsed_ms - self.elapsed_ms())
        return max(1, ceil(remaining_ms / 1000))

    def _time_reason(self) -> Optional[str]:
        elapsed = self.elapsed_ms()
        if elapsed >= self.max_elapsed_ms:
            return f"elapsed_ms {elapsed}/{self.max_elapsed_ms}"
        return None

    def model_block_reason(self) -> Optional[str]:
        return self._time_reason() or (
            f"model_calls {self.model_calls}/{self.max_model_calls}"
            if self.model_calls >= self.max_model_calls
            else None
        )

    def tool_block_reason(self, name: str, *, is_graph: bool = False) -> Optional[str]:
        reason = self._time_reason()
        if reason:
            return reason
        if self.tool_calls >= self.max_tool_calls:
            return f"tool_calls {self.tool_calls}/{self.max_tool_calls}"
        if name == "search_wiki" and self.search_calls >= self.max_search_calls:
            return f"search_calls {self.search_calls}/{self.max_search_calls}"
        if name == "read_wiki":
            if self.read_calls >= self.max_read_calls:
                return f"read_calls {self.read_calls}/{self.max_read_calls}"
            if self.remaining_read_chars() <= 0:
                return f"read_chars {self.read_chars}/{self.max_read_chars}"
        if is_graph and self.graph_calls >= self.max_graph_calls:
            return f"graph_calls {self.graph_calls}/{self.max_graph_calls}"
        return None

    def record_model_call(self, duration_ms: int) -> None:
        self.model_calls += 1
        self.model_duration_ms += max(0, int(duration_ms))

    def record_tool_call(
        self,
        name: str,
        duration_ms: int,
        *,
        is_graph: bool = False,
        read_chars: int = 0,
        index_chars: int = 0,
    ) -> None:
        self.tool_calls += 1
        self.tool_duration_ms += max(0, int(duration_ms))
        if name == "search_wiki":
            self.search_calls += 1
        elif name == "read_wiki":
            self.read_calls += 1
            self.read_chars += max(0, min(int(read_chars), self.remaining_read_chars()))
        elif name == "list_wiki_index":
            self.index_chars += max(0, min(int(index_chars), self.max_index_chars))
        elif is_graph:
            self.graph_calls += 1

    def record_block(self, reason: str) -> None:
        self.blocked_calls += 1
        if reason and reason not in self.blocked_reasons:
            self.blocked_reasons.append(reason)

    def snapshot(self) -> dict:
        return {
            "used": {
                "model_calls": self.model_calls,
                "tool_calls": self.tool_calls,
                "search_calls": self.search_calls,
                "read_calls": self.read_calls,
                "graph_calls": self.graph_calls,
                "read_chars": self.read_chars,
                "index_chars": self.index_chars,
                "elapsed_ms": self.elapsed_ms(),
                "model_duration_ms": self.model_duration_ms,
                "tool_duration_ms": self.tool_duration_ms,
                "blocked_calls": self.blocked_calls,
            },
            "limits": {
                "model_calls": self.max_model_calls,
                "tool_calls": self.max_tool_calls,
                "search_calls": self.max_search_calls,
                "read_calls": self.max_read_calls,
                "graph_calls": self.max_graph_calls,
                "read_chars": self.max_read_chars,
                "index_chars": self.max_index_chars,
                "elapsed_ms": self.max_elapsed_ms,
            },
            "blocked_reasons": list(self.blocked_reasons),
        }

    def summary_text(self) -> str:
        reason_codes = []
        for reason in self.blocked_reasons:
            code = (
                "duplicate"
                if reason == "duplicate_tool_call"
                else reason.split(maxsplit=1)[0]
            )
            if code not in reason_codes:
                reason_codes.append(code)
        return (
            f"calls=m{self.model_calls}/{self.max_model_calls},"
            f"t{self.tool_calls}/{self.max_tool_calls},"
            f"s{self.search_calls}/{self.max_search_calls},"
            f"r{self.read_calls}/{self.max_read_calls},"
            f"g{self.graph_calls}/{self.max_graph_calls} "
            f"chars=w{self.read_chars}/{self.max_read_chars},"
            f"i{self.index_chars}/{self.max_index_chars} "
            f"elapsed_ms={self.elapsed_ms()}/{self.max_elapsed_ms} "
            f"duration_ms=m{self.model_duration_ms},t{self.tool_duration_ms} "
            f"blocked={self.blocked_calls} "
            f"reasons={','.join(reason_codes) if reason_codes else 'none'}"
        )
