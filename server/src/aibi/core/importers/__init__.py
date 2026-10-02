"""Importers (SPEC §12.3, §13.1, §14): files into raw snapshots, layouts and descriptors with
the importer's proposals, through the validation gate into a published release.

- ``errors``: the refusal that stops an import.
- ``confine``: paths confined to the upload area and the import directories, read once.
- ``uploads``: the upload area.
- ``archives``: zip archives and containers, checked entry by entry under limits.
- ``detect``: the parse settings of a text file.
- ``sheets``: workbooks, the only module that calls python-calamine.
- ``worker``: the process, killable and bounded, that reads an import's workbooks and Parquet
  files.
- ``infer``: datatypes, keys, relationships, roles, grain, coverage and identifiers.
- ``describe``: descriptors with curation entries from what was inferred.
- ``files``: the core's own importer, of files, directories and zip archives.
- ``run``: imports and re-imports, from the importer to the release published.
"""
