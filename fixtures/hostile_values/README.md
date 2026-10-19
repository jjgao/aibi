# Hostile values

One CSV file, `items.csv`, whose `kind` column holds hostile texts as its values, for the web's values
seam (D434): `item_id` (`i0` to `i23`, and `i99`), `group` (`g` on every row) and `kind`, which holds
each text below on four rows, as real code points, and is empty on `i99`.
`server/tests/core/analyses/test_openapi_results.py` runs `summary.distribution` over `items.kind`
and writes the answer to `web/tests/fixtures/orchard-answers.json`; its cell asserts that the file's
bytes hold U+202E, U+200B and U+2060 and that each text comes back exactly.

| Value | What it probes |
|---|---|
| `<img src=x onerror=alert(1)>` | markup and an event handler |
| U+202E then `txt.exe` | a right-to-left override |
| `</bdi><b>bold</b><bdi>` | the isolate's end tag, and an element |
| 5,000 `x` | a long value |
| `a`, U+200B, `b`, U+2060, `c` | invisible format characters |
| `javascript:alert(1)` | a script URL, which is text |
