"""Stdio subprocess fixture with explicit test-only keys and an in-memory ledger."""

from agent_backend import Backend

from checkedflow.agents.mcp import create_server

if __name__ == "__main__":
    create_server(Backend().gateway()).run(transport="stdio")
