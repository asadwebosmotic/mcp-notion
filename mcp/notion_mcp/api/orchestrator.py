"""Gemini + MCP Orchestrator.

This module is the *brain* of the system.  It:
1. Receives a user message and session history.
2. Sends it to Gemini Flash along with MCP tool declarations.
3. When Gemini returns function_call parts, routes them through the MCP
   Bridge (which calls the MCP server).
4. Feeds MCP responses back to Gemini until it produces a text answer.

The orchestrator never imports tool Python functions directly — it only
knows tool *schemas* obtained from MCP at startup.
"""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from google import genai
from google.genai import types

from notion_mcp.api.mcp_bridge import get_bridge
from notion_mcp.api.monitoring import MetricsSnapshot, _has_error
from notion_mcp.api.session_store import Session

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, dict[str, Any]], Awaitable[None]]

# System prompt that guides Gemini's behaviour
SYSTEM_INSTRUCTION = (
    "You are an advanced Workspace Assistant powered by Gemini, the Notion MCP server, "
    "and the Slack API.\n"
    "You have access to tools that interact with the user's Notion workspace AND "
    "their Slack workspace.\n\n"
    "GUIDELINES:\n"
    "1. Never guess or hallucinate. Always call tools to fetch real data.\n"
    "2. When asked about counts of databases/pages, call `search` with appropriate filter_value.\n"
    "3. Remember IDs from previous messages — never ask the user for an ID you already know.\n"
    "4. Always display IDs alongside names so the user can reference them later.\n"
    "5. Present results in a clean, readable format.\n\n"
    "SLACK-SPECIFIC GUIDELINES:\n"
    "6. Use `slack_list_channels` to discover channel IDs before reading history or posting.\n"
    "7. Use `slack_list_users` to resolve user IDs to real names when displaying messages.\n"
    "8. When posting to Slack, always confirm the channel and content with the user first.\n"
    "9. Slack tools are prefixed with `slack_` — Notion tools are not prefixed.\n\n"
    "GOOGLE-SPECIFIC GUIDELINES:\n"
    "10. Use `gmail_list_messages` to search emails, `gmail_get_message` to read full content.\n"
    "11. Use `gmail_send_message` to send emails — always confirm recipient, subject, and body with the user first.\n"
    "12. Use `calendar_list_events` to check the user's schedule before suggesting meeting times.\n"
    "13. Use `calendar_create_event` to add events — always confirm details with the user first.\n"
    "14. Google tools are prefixed with `gmail_` and `calendar_`.\n"
)

# Keys that appear in JSON Schema but are not accepted by Gemini
_STRIP_KEYS = {"additionalProperties", "additional_properties", "$schema", "default"}


def _clean_schema(obj: Any) -> Any:
    """Recursively remove keys that Gemini's function calling API rejects."""
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            if k in _STRIP_KEYS:
                continue
            # Gemini does not support "anyOf" — flatten to the first variant
            if k == "anyOf" and isinstance(v, list):
                # Pick the first non-null type if possible
                for variant in v:
                    if isinstance(variant, dict) and variant.get("type") != "null":
                        return _clean_schema(variant)
                # All variants are null; just return string type
                return {"type": "string"}
            cleaned[k] = _clean_schema(v)
        return cleaned
    elif isinstance(obj, list):
        return [_clean_schema(item) for item in obj]
    return obj


def _mcp_schemas_to_function_declarations(
    mcp_tools: list[dict[str, Any]],
) -> list[types.FunctionDeclaration]:
    """Convert MCP tool schemas into Gemini FunctionDeclaration objects."""
    declarations = []
    for tool in mcp_tools:
        params_schema = copy.deepcopy(tool.get("parameters", {}))
        # Clean the schema of unsupported keys
        params_schema = _clean_schema(params_schema)

        decl = types.FunctionDeclaration(
            name=tool["name"],
            description=tool.get("description", ""),
            parameters_json_schema=params_schema if params_schema else None,
        )
        declarations.append(decl)
    return declarations


def _summarize_tool_result(result: Any) -> dict[str, Any]:
    """Build a compact summary for UI progress events."""
    if isinstance(result, dict):
        summary: dict[str, Any] = {}
        if "success" in result:
            summary["success"] = result["success"]
        if "error" in result:
            summary["error"] = result["error"]
        if "message" in result:
            summary["message"] = result["message"]
        if "count" in result:
            summary["count"] = result["count"]
        elif isinstance(result.get("results"), list):
            summary["count"] = len(result["results"])
        elif isinstance(result.get("messages"), list):
            summary["count"] = len(result["messages"])
        return summary or {"type": "object"}
    if isinstance(result, list):
        return {"count": len(result)}
    return {"type": type(result).__name__}


class Orchestrator:
    """Stateless orchestrator — each call gets session history explicitly."""

    def __init__(self, gemini_api_key: str) -> None:
        self._client = genai.Client(api_key=gemini_api_key)

    async def chat(
        self,
        user_message: str,
        session: Session,
        *,
        model: str = "gemini-2.5-flash",
        max_tool_rounds: int = 10,
        progress_callback: ProgressCallback | None = None,
    ) -> str:
        """Process a single user message, potentially calling MCP tools.

        Returns the final assistant text response.
        """
        async def emit(event: str, data: dict[str, Any]) -> None:
            if progress_callback is not None:
                await progress_callback(event, data)

        await emit("status", {"label": "Connecting to MCP tools"})
        bridge = await get_bridge()

        # Initialise per-turn metrics
        metrics = MetricsSnapshot(session_id=session.session_id, model=model)

        # Build Gemini tool declarations from MCP schemas
        func_decls = _mcp_schemas_to_function_declarations(bridge.tools)
        gemini_tools = [types.Tool(function_declarations=func_decls)]

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=gemini_tools,
            temperature=0.2,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True,
            ),
        )

        # Build conversation contents from session history + new user msg
        contents = list(session.history)  # shallow copy
        contents.append({"role": "user", "parts": [{"text": user_message}]})

        tool_call_log: list[dict[str, Any]] = []

        for _round in range(max_tool_rounds):
            await emit(
                "status",
                {
                    "label": "Gemini is deciding whether a tool is needed",
                    "round": _round + 1,
                    "tools_count": len(bridge.tools),
                },
            )

            llm_start = time.monotonic()
            response = self._client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            llm_latency = (time.monotonic() - llm_start) * 1000

            # Record LLM token usage if available
            usage = response.usage_metadata
            prompt_tokens = usage.prompt_token_count if usage else 0
            candidate_tokens = usage.candidates_token_count if usage else 0
            metrics.record_llm_call(prompt_tokens, candidate_tokens, llm_latency)

            # Check if the response contains function calls
            function_calls = []
            text_parts = []

            if not response.candidates:
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    reason = response.prompt_feedback.block_reason
                    raise RuntimeError(f"Gemini blocked the request: {reason}")
                raise RuntimeError("Gemini returned no candidates — the prompt may have been blocked.")

            for candidate in response.candidates:
                if candidate.content is None:
                    continue
                for part in candidate.content.parts:
                    if part.function_call:
                        function_calls.append(part.function_call)
                    elif part.text:
                        text_parts.append(part.text)

            if not function_calls:
                # No more tool calls — Gemini produced a final answer
                final_text = "\n".join(text_parts) if text_parts else "(no response)"
                await emit("status", {"label": "Final answer ready"})

                # Persist history
                session.history.append(
                    {"role": "user", "parts": [{"text": user_message}]}
                )
                session.history.append(
                    {"role": "model", "parts": [{"text": final_text}]}
                )

                metrics.record_memory_size(session.history)
                metrics.log_summary()
                return final_text

            # Gemini wants to call tools — append its request to contents
            fc_parts = []
            for fc in function_calls:
                fc_parts.append(types.Part(function_call=fc))
            contents.append({"role": "model", "parts": fc_parts})

            # Execute each tool call through MCP
            fr_parts = []
            for fc in function_calls:
                name = fc.name
                args = dict(fc.args) if fc.args else {}

                logger.info("[Orchestrator] Tool call: %s(%s)", name, args)
                tool_call_log.append({"tool": name, "args": args})
                await emit("tool_call", {"name": name, "args": args})

                tool_start = time.monotonic()
                result = await bridge.call_tool(name, args)
                tool_latency = (time.monotonic() - tool_start) * 1000

                # Detect tool failure from result payload
                tool_error = _has_error(result)
                metrics.record_tool_call(
                    tool=name,
                    args=args,
                    success=tool_error is None,
                    latency_ms=tool_latency,
                    error=tool_error,
                )

                await emit(
                    "tool_result",
                    {"name": name, "summary": _summarize_tool_result(result)},
                )

                fr_parts.append(
                    types.Part.from_function_response(
                        name=name,
                        response={"result": result},
                    )
                )

            contents.append({"role": "user", "parts": fr_parts})

        # Exhausted tool rounds
        metrics.record_memory_size(session.history)
        metrics.log_summary(warning="max_tool_rounds_reached")
        return "(Reached maximum tool call rounds without a final answer)"
