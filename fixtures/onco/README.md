# Synthetic cBioPortal studies

`demo_study/` is a small study in cBioPortal's format (meta files, a patient and a sample
clinical file, a mutation profile and a case list), written by hand for aibi's oncology pack
(M4.2a-1b): no row describes a real person, and no file is taken from a real study (a study such
as `msk_chord_2024` is CC BY-NC-ND and stays out of the repository). The pack imports its
clinical files and notes the rest:

```bash
scripts/testsite.sh import-study demo_onco fixtures/onco/demo_study
```

The patient file holds missing values (`NA`, `[Not Available]`, an empty cell), a value with a
trailing space (a padded cell, read as written and noted) and a `BOOLEAN` column; the sample file
holds a bounded number (`>9`, which the core counts as a value it cannot parse).
