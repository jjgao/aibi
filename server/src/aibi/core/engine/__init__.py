"""The reference evaluator (SPEC §13.3): the executable definition of §6 and of the resolution
steps of §7.6, row by row over in-memory tables, with no SQL.

- ``truth``: three-valued truth values with reasons and flags (§6.3).
- ``units``: pinned conversion factors for numeric predicates (§6.4).
- ``data``: cells, tables and releases in memory, with their indexes (§6.2, §12.2).
- ``graph``: the table graph and its paths (§6.1).
- ``resolved``: the resolved clause tree, the canonical form before sorting and hashing.
- ``resolve``: a document resolved against releases, or refused (§6.4–§6.5, §7.2, §7.6).
- ``evaluate``: truth values per unit and the cohort's accounting (§6.4–§6.6).
- ``build``: small releases built in code from plain values, for tests and examples.
"""
