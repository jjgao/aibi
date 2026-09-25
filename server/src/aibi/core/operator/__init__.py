"""The operator surface (SPEC §11.2): the operations reserved for people, never tools.

- ``auth``: the curator token, operator names and CSRF tokens, with the standard library and
  ``aibi.core.schema`` alone, so that the CLI imports no server module (D261–D263).
- ``router``: the operator router, which the application mounts at ``/operator`` behind request
  protection and the MCP transport never mounts (D264–D267, D269).
- ``client``: the router's operations over HTTP.
- ``cli``: ``aibi``, the operator CLI, which talks to the router over HTTP only (D268).

Nothing here is imported by this package itself, so the CLI loads no web framework.
"""
