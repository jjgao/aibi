"""The analysis registry and the core's analyses (SPEC §8, §9; M3).

- ``stats``: the statistical methods, deterministic as §9.3 requires, held to R (D321).
- ``disclosure``: the disclosure of the counts a result adds, each predicate's split (§8.4, D320).
- ``existence``: ``compare.existence``, its entry, values, readback and disclosure (D319).
- ``registry``: the registry of the core's and the packs' analyses, and applicability (D316).
- ``views``: phase 2 of canonicalisation, the views checked against their analyses (D317).
- ``charts``: Vega-Lite specifications from a result's values (§8.5, D322).
- ``results``: a view's result envelope, from its values and its derivation (D318).
"""
