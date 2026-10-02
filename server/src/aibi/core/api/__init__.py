"""The HTTP application (SPEC §11, §12.1, §14): one process serving every router behind one
request protection.

- ``config``: the server's configuration, read from one TOML file at start (D253, D254).
- ``origins``: parsing hosts and origins, and the loopback names.
- ``rates``: token buckets per client (D259).
- ``protection``: the middleware that protects every router and mount (D255–D263).
- ``errors``: refusals as the one error shape over HTTP, with a status for each (D265).
- ``routes``: the public API's own routes.
- ``tools``: the public tools over HTTP, at ``/api/tools/<name>`` (D280).
- ``app``: the application, with the tools, the MCP transport, the operator router and the
  mounts behind the middleware.
- ``serve``: ``aibi-server``, which checks the configuration, makes tokens and runs uvicorn.
"""
