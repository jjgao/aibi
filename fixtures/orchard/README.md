# The orchard

Forty trees and sixty harvests: the orchard of `server/tests/core/catalog/world.py`
(`orchard_files(rows=40, harvests=60)`, which `server/tests/core/catalog/test_web_fixtures.py`
holds these files equal to), the dataset the web client's unit fixtures are the server's answers
over (`web/tests/fixtures/orchard-answers.json`). The web's end-to-end matrix imports it for the
catalogue and dataset screens (D431, `web/e2e/catalogue.spec.ts`).

| File | What it holds |
|---|---|
| `trees.csv` | 40 trees: a string key, a variety (three categories), a date, a height missing (`NA`) for every seventh tree, a `;` list of tags |
| `harvests.csv` | 60 harvests, each of a tree (`tree_id`): a weight and a grade |
