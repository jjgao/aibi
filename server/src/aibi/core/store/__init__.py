"""The store (SPEC §12.2, §12.3): blobs, releases, raw snapshots, typed tables, rebuilds, the
app DB, deletion, withdrawal and erasure, and the release lifecycle.

- ``blobs``: content-addressed, immutable files, written atomically.
- ``sources``: raw snapshots, their canonical string form, and reading them into rows.
- ``cells``: typed cells and their states from source values and the column's descriptor.
- ``derive``: derived columns (§5.7).
- ``tombstones``: the removals a re-import respects (D240).
- ``writes``: the pack checks on every descriptor write, and versions (D243, D247).
- ``edits``: applying a change's edits to a draft (D245).
- ``carry``: carrying curation forward on re-import (D239).
- ``sessions``: curation sessions (D244–D246).
- ``proposals``: the proposal queue, the curation proposers and the curation queue (D248–D250).
"""
