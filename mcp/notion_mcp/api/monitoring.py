"""Centralised monitoring — LLM cost, token usage, latency, MCP calls, tool failures, memory size."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_GEMINI_PRICES: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = _GEMINI_PRICES.get(model, _GEMINI_PRICES["gemini-2.5-flash"])
    input_cost = (input_tokens / 1_000_000) * prices["input"]
    output_cost = (output_tokens / 1_000_000) * prices["output"]
    return round(input_cost + output_cost, 8)


def _has_error(result: Any) -> str | None:
    if isinstance(result, dict):
        err = result.get("error")
        if err:
            return str(err)
    return None


@dataclass
class ToolCallRecord:
    tool: str
    args: dict[str, Any]
    success: bool
    latency_ms: float
    error: str | None = None


@dataclass
class MetricsSnapshot:
    """Collects and logs all observability signals for a single chat turn."""

    session_id: str
    model: str = "gemini-2.5-flash"

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_latency_ms: float = 0.0

    tool_calls: list[ToolCallRecord] = field(default_factory=list)

    start_time: float = field(default_factory=time.monotonic)
    session_history_size: int = 0
    session_history_chars: int = 0

    # ------------------------------------------------------------------
    # LLM call tracking
    # ------------------------------------------------------------------
    def record_llm_call(
        self,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
    ) -> None:
        self.llm_calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_tokens += input_tokens + output_tokens
        self.llm_latency_ms += latency_ms

        cost_here = _estimate_cost(self.model, input_tokens, output_tokens)
        logger.info(
            "LLM call #%d | in=%d out=%d lat=%.0fms cost=$%.6f total=%d",
            self.llm_calls,
            input_tokens,
            output_tokens,
            latency_ms,
            cost_here,
            input_tokens + output_tokens,
        )

    # ------------------------------------------------------------------
    # Tool call tracking
    # ------------------------------------------------------------------
    def record_tool_call(
        self,
        tool: str,
        args: dict[str, Any],
        success: bool,
        latency_ms: float,
        error: str | None = None,
    ) -> None:
        record = ToolCallRecord(
            tool=tool,
            args=args,
            success=success,
            latency_ms=latency_ms,
            error=error,
        )
        self.tool_calls.append(record)

        level = logging.WARNING if not success else logging.DEBUG
        extra = f" error={error}" if error else ""
        logger.log(
            level,
            "Tool call %s | ok=%s lat=%.0fms%s",
            tool,
            success,
            latency_ms,
            extra,
        )

    # ------------------------------------------------------------------
    # Memory / session size tracking
    # ------------------------------------------------------------------
    def record_memory_size(self, history: list[dict[str, Any]]) -> None:
        self.session_history_size = len(history)
        self.session_history_chars = sum(
            len(json.dumps(m, ensure_ascii=False)) for m in history
        )
        logger.info(
            "Session memory: %d msgs, %d chars",
            self.session_history_size,
            self.session_history_chars,
        )

    # ------------------------------------------------------------------
    # Final summary (call once at end of chat turn)
    # ------------------------------------------------------------------
    def log_summary(self, *, warning: str | None = None) -> None:
        elapsed = time.monotonic() - self.start_time
        total_cost = _estimate_cost(self.model, self.input_tokens, self.output_tokens)
        tool_failures = sum(1 for t in self.tool_calls if not t.success)

        summary = {
            "event": "chat_completed",
            "session_id": self.session_id,
            "duration_ms": round(elapsed * 1000, 2),
            "llm_calls": self.llm_calls,
            "llm_latency_ms": round(self.llm_latency_ms, 2),
            "total_cost": total_cost,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "tool_calls": len(self.tool_calls),
            "tool_failures": tool_failures,
            "memory_messages": self.session_history_size,
            "memory_chars": self.session_history_chars,
        }

        log_fn = logger.warning if warning else logger.info
        log_fn("Metrics summary: %s", json.dumps(summary))
