"""The MCP transport (SPEC §11.1, §14): the public tools and the descriptor resources over the
official MCP Python SDK.

- ``calls``: tool calls as both transports run them, in worker threads of their own, each with a
  wall-clock limit (D278).
- ``server``: the MCP server and its stateless streamable HTTP transport at ``/mcp`` (D278, D279).

The transport never mounts the operator router and imports none of it (§11.2, D264).
"""
