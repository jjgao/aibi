"""The catalogue (SPEC §8.1, §8.4, §11.1, §12.2): what agents and people can learn of the datasets
before any query, through the public tools.

- ``disclosure``: catalogue statistics as they are served, under the disclosure settings, with
  their ``stat:`` references (D271, D272).
- ``index``: the catalogue index of the latest published releases, in the app DB (D273).
- ``service``: the service functions the MCP server and the HTTP API share (D274–D279).
- ``tools``: the public tools, their descriptions and how a call is read (D277, D278, D280).

The statistics themselves are counted when a release is built (``store.statistics``, D270). No
module here performs an operator operation (§11.2).
"""
