# Hostile headers

One CSV file whose six headers are hostile texts, for the web's end-to-end matrix (D431,
`web/e2e/catalogue.spec.ts`): each survives the import as a column's label
(`server/tests/core/catalog/test_web_fixtures.py`), and the dataset screen must show each as text,
never as markup.

| Header | What it probes |
|---|---|
| `<img src=x onerror=alert(1)>` | markup and an event handler |
| U+202E then `txt.exe` | a right-to-left override, shown as a visible escape |
| `</bdi><b>bold</b><bdi>` | the isolate's end tag, and an element |
| `h` and 2,999 `x` | a label of 3,000 characters, cut at 2,000 and marked |
| `a`, U+200B, `b`, U+2060, `c` | invisible format characters, shown as visible escapes |
| `javascript:alert(1)` | a script URL, which is text and never a link |
