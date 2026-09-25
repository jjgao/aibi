# A lending library

A made-up library, for the file importer's tests (`server/tests/core/importers/test_library.py`);
`../library_next` is its next export, for the re-import tests.
Importing this directory gives six tables; `formats/` is a subdirectory, so it is skipped and
noted.

| File | What it tests |
|---|---|
| `members.csv` | a `# export` preamble line (`skip_rows` 1); `NA` (a conventional missing code) and `refused` (counted, not declared) among integer ages; a `;` list of interests; 24 distinct names, proposed as an identifier |
| `books.tsv` | cp1252 text with accented titles; a category |
| `copies.csv` | an entity child of books, so its coverage is proposed |
| `shelvings.csv` | a link table: a key of two foreign keys, and coverage for both |
| `shelves.parquet` | Parquet, with an `int32` column and a null |
| `loans.xlsx` | a `Loans` sheet of datetimes without offsets (`imported_default`) and an empty `Notes` sheet |
| `formats/loans.ods` | the same workbook as OpenDocument |

`loans.xlsx` and `formats/loans.ods` were written with `build_xlsx` and `build_ods` of
`server/tests/core/importers/conftest.py`, and `shelves.parquet` with
`pyarrow.parquet.write_table`. There is no `.xls` fixture: LibreOffice's Calc, which would convert
one, is not installed where these were made.
