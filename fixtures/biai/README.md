# biai examples

`patients.csv`, `treatments.csv` and `clinical_trial_data.xlsx` are copied byte for byte from
`example_data/` of [jjgao/biai](https://github.com/jjgao/biai) at commit
`1a53a4766993a902caed1105551b7a51168c854a`, the tool aibi replaces. They are small, made-up
clinical records.

The data is biomedical; the core that imports it is not. Their only test module is
`server/tests/core/importers/test_biai_examples.py`, which checks what the file importer
proposes for them.
