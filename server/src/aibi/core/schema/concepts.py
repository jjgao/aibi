"""The core's concepts (SPEC §5.7). They are domain-neutral; packs add their own."""

from aibi.core.schema.descriptors import (
    ConceptDescriptor,
    ConceptFields,
    PermissibleValue,
    PermissibleValues,
)


def _concept(id: str, label: str, definition: str, fields: ConceptFields) -> ConceptDescriptor:
    return ConceptDescriptor(
        kind="concept", id=id, version=1, label=label, definition=definition, fields=fields
    )


CORE_CONCEPTS: tuple[ConceptDescriptor, ...] = (
    _concept(
        "core:person",
        "Person",
        "Rows are individual people.",
        ConceptFields(sort="table"),
    ),
    _concept(
        "core:age_years",
        "Age in years",
        "Age in years at the time the value refers to.",
        ConceptFields(sort="value", units="a"),
    ),
    _concept(
        "core:sex",
        "Sex",
        "Sex, with the values female and male.",
        ConceptFields(
            sort="value",
            permissible_values=PermissibleValues(
                values=[PermissibleValue(value="female"), PermissibleValue(value="male")]
            ),
        ),
    ),
    _concept(
        "core:origin.birth",
        "Birth",
        "Time zero is the unit's birth.",
        ConceptFields(sort="time_origin"),
    ),
    _concept(
        "core:origin.entry",
        "Entry",
        "Time zero is the unit's entry into the data or its observation.",
        ConceptFields(sort="time_origin"),
    ),
    _concept(
        "core:origin.calendar",
        "Calendar",
        "Times are absolute dates.",
        ConceptFields(sort="time_origin"),
    ),
)
