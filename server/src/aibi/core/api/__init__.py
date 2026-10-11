"""The HTTP application (SPEC §11, §12.1, §14): one process serving every router behind one
request protection.

- ``config``: the server's configuration, read from one TOML file at start (D253, D254).
- ``origins``: parsing hosts and origins, and the loopback names.
- ``rates``: token buckets per client (D259).
- ``protection``: the middleware that protects every router and mount (D255–D263).
- ``errors``: refusals as the one error shape over HTTP, with a status for each (D265).
- ``routes``: the public API's own routes.
- ``tools``: the public tools over HTTP, at ``/api/tools/<name>`` (D280).
- ``markup``: HTML that text cannot turn into markup (D312).
- ``chrome``: what the catalogue's pages share, their refusals and page paths included (D311,
  D313, D314).
- ``page``: the read-only catalogue page, at ``/`` and ``/datasets/<dataset>`` (D311, D313).
- ``bundle``: a built web bundle, loaded at start, its two documents written by the server, and
  its routes, which replace the catalogue page's (D410–D412).
- ``app``: the application, with the tools, the MCP transport, the catalogue page or the web
  bundle, the operator router and the mounts behind the middleware.

``aibi.core.classify`` holds the record of each request that the middleware makes and the rest
reads (D414).
- ``serve``: ``aibi-server``, which checks the configuration, makes tokens and runs uvicorn.
"""
