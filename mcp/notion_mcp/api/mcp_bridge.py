"""MCP Bridge — connects to the MCP server over streamable-http.

Instead of managing the MCP server as a subprocess with stdio pipes (which
conflicts with uvicorn's asyncio task management), this bridge:

1. Launches the MCP server as a background HTTP process on a dedicated port.
2. Connects to it using the ``streamablehttp_client`` from the MCP SDK.

This cleanly separates the process lifecycle from the asyncio event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)

MCP_SERVER_PORT = int(os.environ.get("MCP_SERVER_PORT", "3100"))
MCP_SERVER_URL = f"http://localhost:{MCP_SERVER_PORT}/mcp"


class MCPBridge:
    """Manages a connection to the MCP server over HTTP."""

    def __init__(self) -> None:
        self._exit_stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self._server_process: subprocess.Popen | None = None
        self._tools: list[dict[str, Any]] = []
        self._tool_names: set[str] = set()

    async def connect(self) -> None:
        """Start the MCP server subprocess and connect via HTTP."""
        # 1. Launch MCP server as a separate process with --http
        env = os.environ.copy()
        self._server_process = subprocess.Popen(
            [sys.executable, "-m", "notion_mcp.mcp_server", "--http", "--port", str(MCP_SERVER_PORT)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info(
            "MCP server subprocess started (PID %d) on port %d",
            self._server_process.pid,
            MCP_SERVER_PORT,
        )

        # 2. Wait for the server to become ready (retry a few times)
        await self._wait_for_server()

        # 3. Connect as an MCP client via streamable-http
        read, write, _ = await self._exit_stack.enter_async_context(
            streamablehttp_client(MCP_SERVER_URL)
        )
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await self._session.initialize()

        # 4. Discover available tools
        tools_result = await self._session.list_tools()
        self._tools = []
        self._tool_names = set()
        for tool in tools_result.tools:
            schema = {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema,
            }
            self._tools.append(schema)
            self._tool_names.add(tool.name)

        logger.info(
            "MCP Bridge connected via HTTP — %d tools discovered: %s",
            len(self._tools),
            sorted(self._tool_names),
        )

    async def _wait_for_server(self, timeout: float = 15.0) -> None:
        """Poll the MCP server until it's listening on its port or timeout."""
        import socket as _socket

        start = time.monotonic()
        while time.monotonic() - start < timeout:
            # Check if subprocess exited prematurely
            if self._server_process and self._server_process.poll() is not None:
                raise RuntimeError(
                    f"MCP server subprocess exited early with code "
                    f"{self._server_process.returncode}"
                )
            try:
                sock = _socket.create_connection(
                    ("localhost", MCP_SERVER_PORT), timeout=2.0,
                )
                sock.close()
                took = time.monotonic() - start
                logger.info(
                    "MCP server ready (port %d) after %.1fs",
                    MCP_SERVER_PORT, took,
                )
                return
            except (OSError, _socket.timeout, ConnectionRefusedError) as exc:
                logger.debug("Waiting for MCP server (%.1fs): %s", time.monotonic() - start, exc)
                await asyncio.sleep(0.5)

        raise RuntimeError(
            f"MCP server did not start within {timeout}s on port {MCP_SERVER_PORT}"
        )

    async def disconnect(self) -> None:
        """Cleanly shut down the MCP session and server process."""
        await self._exit_stack.aclose()
        self._session = None
        if self._server_process:
            self._server_process.terminate()
            try:
                self._server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._server_process.kill()
            logger.info("MCP server subprocess terminated")
            self._server_process = None

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Return the list of tool schemas discovered from MCP."""
        return self._tools

    @property
    def tool_names(self) -> set[str]:
        return self._tool_names

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a tool through the MCP server and return its result."""
        if self._session is None:
            raise RuntimeError("MCP Bridge is not connected")
        if name not in self._tool_names:
            return {"error": f"Tool '{name}' not found in MCP server"}

        logger.info("MCP Bridge -> calling tool '%s' with args: %s", name, arguments)
        start = time.monotonic()
        result = await self._session.call_tool(name, arguments)
        latency = (time.monotonic() - start) * 1000

        # MCP returns a CallToolResult with .content list
        parts = []
        for content_item in result.content:
            if hasattr(content_item, "text"):
                try:
                    parts.append(json.loads(content_item.text))
                except json.JSONDecodeError:
                    parts.append(content_item.text)
            else:
                parts.append(str(content_item))

        output = parts[0] if len(parts) == 1 else parts

        # Check for tool-level errors in the output
        error = None
        if isinstance(output, dict):
            err_val = output.get("error")
            if err_val:
                error = str(err_val)

        if error:
            logger.warning(
                "MCP Bridge <- tool '%s' failed in %.0fms: %s",
                name, latency, error,
            )
        else:
            logger.info(
                "MCP Bridge <- tool '%s' OK in %.0fms",
                name, latency,
            )
        return output


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_bridge: MCPBridge | None = None


async def get_bridge() -> MCPBridge:
    """Return (and lazily initialise) the global MCPBridge singleton."""
    global _bridge
    if _bridge is None or _bridge._session is None:
        _bridge = MCPBridge()
        await _bridge.connect()
    return _bridge
