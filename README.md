# aibi

AI-native cohort exploration over related tables: spreadsheets, files or database tables.
The core is domain-agnostic; domains such as oncology (including cBioPortal studies) are added
as packs.

Researchers and agents define cohorts with a declarative JSON document and run registered
analyses over versioned study releases. Every number comes back with how it was derived, over
which patients, from which data release, and what the data could not account for.

Status: design. See [SPEC.md](SPEC.md).
