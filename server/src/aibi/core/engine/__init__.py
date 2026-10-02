"""The reference evaluator (SPEC §13.3): the executable definition of §6 and of §7.6, row by row
over in-memory tables, with no SQL; and the canonical forms, ids and digests of M2.

- ``truth``: three-valued truth values with reasons and flags (§6.3).
- ``units``: pinned conversion factors for numeric predicates (§6.4).
- ``data``: cells, tables and releases in memory, with their indexes (§6.2, §12.2).
- ``graph``: the table graph and its paths (§6.1).
- ``resolved``: the resolved clause tree, the canonical form before sorting and hashing.
- ``resolve``: a document resolved against releases, or refused (§6.4–§6.5, §7.2, §7.6).
- ``evaluate``: truth values per unit and the cohort's accounting (§6.4–§6.6).
- ``canonical``: canonical cohorts and their ids, a view's ids from its parts (§7.6).
- ``ids``: hashes, rounding and digests (§7.6, §9.3).
- ``counts``: the digested part of a cohort's count (§8.1).
- ``build``: small releases built in code from plain values, for tests and examples.
"""
