"""MCP stdio protocol fixture, not a model service."""

from typing import Any

from mcp.server.fastmcp import FastMCP
from model import generate as reply

server = FastMCP("context-protocol-fixture")


@server.tool()
def generate(request: dict) -> Any:
    return reply(request)


if __name__ == "__main__":
    server.run(transport="stdio")
