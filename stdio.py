# -*- coding: utf-8 -*-
"""Robust stdio transport helpers for MCP servers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from io import TextIOWrapper
import sys
from typing import Any

import anyio
import anyio.lowlevel
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
import mcp.types as types
from mcp.shared.message import SessionMessage


@asynccontextmanager
async def robust_stdio_server(stdin=None, stdout=None):
    """MCP stdio transport that ignores blank terminal input lines."""
    if stdin is None:
        stdin = anyio.wrap_file(
            TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        )
    if stdout is None:
        stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))

    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]

    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    async def stdin_reader():
        try:
            async with read_stream_writer:
                async for line in stdin:
                    if not line.strip():
                        continue
                    try:
                        message = types.JSONRPCMessage.model_validate_json(line)
                    except Exception as exc:
                        await read_stream_writer.send(exc)
                        continue
                    await read_stream_writer.send(SessionMessage(message))
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async def stdout_writer():
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    output = session_message.message.model_dump_json(
                        by_alias=True, exclude_none=True
                    )
                    await stdout.write(output + "\n")
                    await stdout.flush()
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(stdin_reader)
        task_group.start_soon(stdout_writer)
        yield read_stream, write_stream


async def run_fastmcp_stdio_async(fastmcp: Any) -> None:
    async with robust_stdio_server() as (read_stream, write_stream):
        await fastmcp._mcp_server.run(
            read_stream,
            write_stream,
            fastmcp._mcp_server.create_initialization_options(),
        )


def run_fastmcp_stdio(fastmcp: Any) -> None:
    anyio.run(lambda: run_fastmcp_stdio_async(fastmcp))
