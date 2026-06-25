"""Entry point: run the TNSO MCP server over stdio (`python -m tnso_mcp_server`)."""

from __future__ import annotations

import asyncio

from mcp.server.stdio import stdio_server

from .server import create_server


async def _run() -> None:
    """Create the server and serve it over the stdio transport until EOF."""
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Console-script entry point: run the stdio server in an event loop."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
