from __future__ import annotations

import anyio

from memory_server.stdio import robust_stdio_server


class _MemoryWriter:
    def __init__(self) -> None:
        self.chunks: list[str] = []

    async def write(self, data: str) -> None:
        self.chunks.append(data)

    async def flush(self) -> None:
        pass


def test_blank_stdio_input_is_ignored():
    async def run():
        send, recv = anyio.create_memory_object_stream[str](10)
        writer = _MemoryWriter()

        async def feed():
            async with send:
                await send.send("\n")
                await send.send("   \n")

        async def read():
            async with robust_stdio_server(stdin=recv, stdout=writer) as (read_stream, write_stream):
                await write_stream.aclose()
                with anyio.fail_after(1):
                    try:
                        await read_stream.receive()
                    except anyio.EndOfStream:
                        return
                raise AssertionError("Blank stdio input should not produce a message.")

        async with anyio.create_task_group() as tg:
            tg.start_soon(feed)
            tg.start_soon(read)

    anyio.run(run)
