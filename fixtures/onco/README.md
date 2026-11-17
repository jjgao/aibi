# Synthetic cBioPortal studies

`demo_study/` is a small study in cBioPortal's format (meta files, a patient and a sample
clinical file, a mutation profile, a structural variant profile, a discrete copy-number matrix
and two case lists), written by hand for aibi's oncology pack (M4.2a-1b; the structural variants
from M4.2b-4, SPEC D445: three well-known fusions on three samples; the copy-number matrix from
M4.2b-3, SPEC D446: four genes by eight samples, with a `-1.5`, a sample whose every cell is `NA`
and two samples the matrix leaves out, so that they are not covered): no row describes a real
person, and no file is taken from a real study (a study such as `msk_chord_2024` is CC BY-NC-ND
and stays out of the repository). The pack imports its clinical files, its MAF, its case lists,
its structural variants and its copy-number matrix (with the `<study>_cna` case list it needs):

```bash
scripts/testsite.sh import-study demo_onco fixtures/onco/demo_study
```

The patient file holds missing values (`NA`, `[Not Available]`, an empty cell), a value with a
trailing space (a padded cell, read as written and noted) and a `BOOLEAN` column; the sample file
holds a bounded number (`>9`, which the core counts as a value it cannot parse).
