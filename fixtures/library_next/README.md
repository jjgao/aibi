# The lending library's next export

The next export of the made-up library of `../library`, for the re-import tests
(`server/tests/core/importers/test_reimport.py`, SPEC §12.3). Re-importing this directory over a
release of `../library` changes what the table below says, and nothing else; finer variants (a
table removed, columns moved, headers repeated or empty) are made from these bytes by the
`library_variant` fixture of `server/tests/core/importers/conftest.py`.

| File | What it tests |
|---|---|
| `members.csv` | member `m24` is gone (the first step of an erasure); `m11`'s age is `41.5`, so `age` is inferred a `number` instead of an `integer` (a changed inference); a new `branch` column (a descriptor added) |
| `reviews.csv` | a new table, with a new proposed relationship to `books` |
| `books.tsv`, `copies.csv`, `shelvings.csv`, `shelves.parquet`, `loans.xlsx` | byte-identical copies of `../library`'s, so their descriptors are carried forward unchanged |
