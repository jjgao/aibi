"""The store (SPEC §12.2, §12.3): blobs, releases, raw snapshots, typed tables, rebuilds, the
app DB, deletion, withdrawal and erasure.

- ``blobs``: content-addressed, immutable files, written atomically.
- ``sources``: raw snapshots, their canonical string form, and reading them into rows.
- ``cells``: typed cells and their states from source values and the column's descriptor.
- ``derive``: derived columns (§5.7).
"""
