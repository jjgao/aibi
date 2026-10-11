"""A study's meta files (M4.2a-1a, D409): the parse, the classification, the mandatory keys, the
``stable_id`` rule and the study meta's values, each against cBioPortal's facts at 47890bb as
``meta.py`` cites them, with the bound pairs of the plan's §5.

The functions are tested as they are, and through ``discover`` where the order of the checks or a
study's outcome is what is held.
"""

import importlib
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aibi.core.schema.output import DataSegment, TextSegment
from aibi.core.schema.pack_api import Refused

MODULE = "aibi.packs.onco"


@pytest.fixture
def meta() -> ModuleType:
    return importlib.import_module(MODULE + ".meta")


@pytest.fixture
def text_module() -> ModuleType:
    return importlib.import_module(MODULE + ".text")


def refusal_of(call: Callable[[], object]) -> tuple[str, str]:
    """The code and the message, as text, ``call`` refuses with."""
    with pytest.raises(Refused) as raised:
        call()
    (refusal,) = raised.value.refusals
    message = "".join(
        s.text if isinstance(s, TextSegment) else f"«{s.data}»"
        for s in refusal.message
        if isinstance(s, TextSegment | DataSegment)
    )
    return str(refusal.code), message


# --- The parse (C:852-870) ------------------------------------------------------------------------


def test_lines_end_at_universal_newlines_only(meta: ModuleType) -> None:
    """U+2028, U+2029, U+0085, a form feed and a vertical tab are inside a line, as Python's text
    files read it, and not where ``str.splitlines`` would cut it."""
    for inside in ("\u2028", "\u2029", "\x85", "\x0c", "\x0b", "\x1c"):
        raw = f"description: a{inside}data_filename: nope.txt\n".encode()
        assert meta.parse(raw, "meta_x.txt") == {"description": f"a{inside}data_filename: nope.txt"}
    raw = b"a: 1\r\nb: 2\rc: 3\nd: 4"
    assert meta.parse(raw, "meta_x.txt") == {"a": "1", "b": "2", "c": "3", "d": "4"}


def test_one_bom_is_read_past_and_a_second_is_content(meta: ModuleType) -> None:
    assert meta.parse("\ufeffname: x\n".encode(), "m") == {"name": "x"}
    assert meta.parse("\ufeff\ufeffname: x\n".encode(), "m") == {"\ufeffname": "x"}
    assert meta.parse("name: \ufeffx\ufeff\n".encode(), "m") == {"name": "\ufeffx\ufeff"}


def test_two_boms_lose_the_first_key(make_study: Any, discovery: Any, base: Any) -> None:
    """The first key of a study meta behind two BOMs is not the key: its type stands, and the
    mandatory ``name`` is missing (cause rule 2 covers one BOM only)."""
    base["meta_study.txt"] = "\ufeff\ufeffname: x\n".encode() + base["meta_study.txt"].replace(
        b"name: Demo study\n", b""
    )
    refusal = discovery.refusal(make_study(base))
    assert refusal.code == "onco.META_FIELD"
    assert refusal.message[0] == DataSegment(data="meta_study.txt")
    base["meta_study.txt"] = b"\xef\xbb\xbf" + base["meta_study.txt"][6:]
    assert discovery.outcome(make_study(base))[0] == "ok"


def test_blank_lines_are_skipped_by_str_strip(meta: ModuleType) -> None:
    for blank in ("", " ", "\t", "\u00a0", "\u3000 ", "\x1c", "\u2028"):
        assert meta.parse(f"a: 1\n{blank}\nb: 2\n".encode(), "m") == {"a": "1", "b": "2"}


def test_a_line_without_a_colon_is_meta_file(meta: ModuleType) -> None:
    code, message = refusal_of(lambda: meta.parse(b"a: 1\njust words\n", "meta_x.txt"))
    assert (code, message) == ("onco.META_FILE", "«meta_x.txt» has a line without ':'")


def test_not_utf8_is_meta_file(meta: ModuleType) -> None:
    for raw in (b"name: caf\xe9\n", b"name: \xed\xa0\x80\n", b"\xff\xfe"):
        code, message = refusal_of(lambda raw=raw: meta.parse(raw, "meta_x.txt"))
        assert (code, message) == ("onco.META_FILE", "«meta_x.txt» is not UTF-8")


def test_the_key_is_kept_and_the_value_stripped(meta: ModuleType) -> None:
    raw = " data_filename : \t x y \u00a0\u2028\n".encode()
    assert meta.parse(raw, "m") == {" data_filename ": "x y"}
    assert meta.parse(b"a::b: \n", "m") == {"a": ":b:"}
    assert meta.parse(b"a:\n", "m") == {"a": ""}


def test_the_last_of_a_repeated_key_wins(meta: ModuleType) -> None:
    raw = b"data_filename: nope.txt\ndata_filename: data_clinical_sample.txt\n"
    assert meta.parse(raw, "m") == {"data_filename": "data_clinical_sample.txt"}


def test_a_value_holding_u2028_then_a_key_names_nothing(
    make_study: Any, discovery: Any, base: Any
) -> None:
    """The plan's killing case for ``splitlines``: the second ``data_filename`` is inside the
    description's value, so the sample meta still names its file."""
    base["meta_clinical_sample.txt"] += "description: a\u2028data_filename: nope.txt\n".encode()
    assert discovery.outcome(make_study(base))[:2] == ("ok", "data_clinical_sample.txt")


def test_the_meta_name_pattern(meta: ModuleType) -> None:
    named = [
        "meta_study.txt",
        "META_mut.txt",
        "study_meta.txt",
        "x.meta",
        "meta",
        "meta0.txt",
        "a_meta_b",
        "data-meta.txt",
        "Meta-x",
    ]
    not_named = ["metadata.txt", "metastudy.txt", "xmeta.txt", "data.txt", "metá.txt"]
    assert [name for name in named if not meta.meta_named(name)] == []
    assert [name for name in not_named if meta.meta_named(name)] == []


def test_the_exclusions(meta: ModuleType) -> None:
    assert meta.excluded(".meta_study.txt") == "a hidden file"
    assert meta.excluded("meta_study.txt~") == "an editor or OncoKB backup"
    assert meta.excluded("ONCOKB_IMPORT_BACKUP_meta_study.txt") == "an editor or OncoKB backup"
    for kept in ("meta_study.txt", "oncokb_import_backup_meta.txt", "a~b", "x.", "~x"):
        assert meta.excluded(kept) is None


# --- Classification (C:691-729) -------------------------------------------------------------------


def test_the_pair_types_are_the_contract_pairs(meta: ModuleType) -> None:
    schemas = importlib.import_module(MODULE + ".schemas")
    assert tuple(meta.PAIR_TYPES) == schemas.PAIRS


def test_classification_order(meta: ModuleType) -> None:
    classify = meta.classify
    clinical = {"genetic_alteration_type": "CLINICAL", "datatype": "SAMPLE_ATTRIBUTES"}
    assert classify(
        {**clinical, "type_of_cancer": "brca", "cancer_study_identifier": "s"}, "m"
    ) == ("SAMPLE_ATTRIBUTES")
    assert classify({"cancer_study_identifier": "s", "type_of_cancer": "brca"}, "m") == "STUDY"
    study_keys = {"cancer_study_identifier": "s", "type_of_cancer": "brca"}
    for lone in ({"datatype": "X"}, {"genetic_alteration_type": "X"}):
        assert classify({**lone, **study_keys}, "m") == "STUDY", lone  # one key of the pair is none
    study_and_resource = {
        "cancer_study_identifier": "s",
        "type_of_cancer": "b",
        "resource_type": "X",
    }
    assert classify(study_and_resource, "m") == "STUDY"
    assert classify({"type_of_cancer": "brca", "resource_type": "SAMPLE"}, "m") == "CANCER_TYPE"
    for resource, kind in meta.RESOURCE_TYPES.items():
        found = {"cancer_study_identifier": "s", "resource_type": resource}
        assert classify(found, "m") == kind
    for pair, kind in meta.PAIR_TYPES.items():
        alteration, _, datatype = pair.partition(":")
        assert classify({"genetic_alteration_type": alteration, "datatype": datatype}, "m") == kind
    unknown = [
        {"genetic_alteration_type": "FOO", "datatype": "BAR"},
        {"genetic_alteration_type": "CLINICAL", "datatype": "sample_attributes"},
        {"genetic_alteration_type": "MUTATION_EXTENDED", "datatype": "FUSION"},
        {"cancer_study_identifier": "s", "resource_type": "FOO"},
        {"cancer_study_identifier": "s", "resource_type": "sample"},
        {"cancer_study_identifier": "s"},
        {"resource_type": "SAMPLE"},
        {"genetic_alteration_type": "CLINICAL"},
        {},
    ]
    for found in unknown:
        code, message = refusal_of(lambda found=found: classify(found, "meta_x.txt"))
        assert (code, message) == (
            "onco.META_FILE",
            "«meta_x.txt» has no meta file type cBioPortal defines",
        ), found


def test_a_clinical_meta_holding_type_of_cancer_stays_clinical(
    make_study: Any, discovery: Any, base: Any
) -> None:
    base["meta_clinical_sample.txt"] += b"type_of_cancer: brca\n"
    assert discovery.outcome(make_study(base))[:2] == ("ok", "data_clinical_sample.txt")


def test_an_unknown_resource_type_is_refused(make_study: Any, discovery: Any, base: Any) -> None:
    """Stricter: cBioPortal skips it without an error (D409)."""
    base["meta_res.txt"] = b"cancer_study_identifier: demo_study\nresource_type: FOO\n"
    assert discovery.outcome(make_study(base))[:2] == ("onco.META_FILE", "meta_res.txt")


# --- MANDATORY (C:94-371) -------------------------------------------------------------------------

GOLDEN = {
    "CANCER_TYPE": (
        "genetic_alteration_type",
        "datatype",
        "data_filename",
    ),
    "STUDY": (
        "cancer_study_identifier",
        "type_of_cancer",
        "name",
        "description",
    ),
    "SAMPLE_ATTRIBUTES": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "data_filename",
    ),
    "PATIENT_ATTRIBUTES": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "data_filename",
    ),
    "CNA_DISCRETE": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "show_profile_in_analysis_tab",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "CNA_DISCRETE_LONG": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "show_profile_in_analysis_tab",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "CNA_LOG2": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "show_profile_in_analysis_tab",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "CNA_CONTINUOUS": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "show_profile_in_analysis_tab",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "SEG": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "reference_genome_id",
        "data_filename",
        "description",
    ),
    "MUTATION": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "show_profile_in_analysis_tab",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "MUTATION_UNCALLED": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "EXPRESSION": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "show_profile_in_analysis_tab",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "METHYLATION": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "show_profile_in_analysis_tab",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "PROTEIN": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "show_profile_in_analysis_tab",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "GISTIC_GENES": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "reference_genome_id",
        "data_filename",
    ),
    "TIMELINE": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "data_filename",
    ),
    "MUTATION_SIGNIFICANCE": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "data_filename",
    ),
    "GENE_PANEL_MATRIX": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "data_filename",
    ),
    "GSVA_PVALUES": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "source_stable_id",
        "profile_name",
        "profile_description",
        "data_filename",
        "geneset_def_version",
    ),
    "GSVA_SCORES": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "source_stable_id",
        "profile_name",
        "profile_description",
        "data_filename",
        "show_profile_in_analysis_tab",
        "geneset_def_version",
    ),
    "GENERIC_ASSAY_CONTINUOUS": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "generic_assay_type",
        "datatype",
        "stable_id",
        "profile_name",
        "profile_description",
        "data_filename",
        "show_profile_in_analysis_tab",
    ),
    "GENERIC_ASSAY_BINARY": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "generic_assay_type",
        "datatype",
        "stable_id",
        "profile_name",
        "profile_description",
        "data_filename",
        "show_profile_in_analysis_tab",
    ),
    "GENERIC_ASSAY_CATEGORICAL": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "generic_assay_type",
        "datatype",
        "stable_id",
        "profile_name",
        "profile_description",
        "data_filename",
        "show_profile_in_analysis_tab",
    ),
    "STRUCTURAL_VARIANT": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "show_profile_in_analysis_tab",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "SAMPLE_RESOURCES": (
        "cancer_study_identifier",
        "resource_type",
        "data_filename",
    ),
    "PATIENT_RESOURCES": (
        "cancer_study_identifier",
        "resource_type",
        "data_filename",
    ),
    "STUDY_RESOURCES": (
        "cancer_study_identifier",
        "resource_type",
        "data_filename",
    ),
    "RESOURCES_DEFINITION": (
        "cancer_study_identifier",
        "resource_type",
        "data_filename",
    ),
}
"""cbioportal-core 47890bb, ``cbioportal_common.py`` ``META_FIELD_MAP`` (C:94-371): each type's
members marked mandatory, in its order (re-read by hand from C at the pin, not derived from the
pack's table)."""


def test_mandatory_is_the_golden(meta: ModuleType) -> None:
    assert dict(meta.MANDATORY) == GOLDEN
    assert set(meta.MANDATORY) == {
        *meta.PAIR_TYPES.values(),
        *meta.RESOURCE_TYPES.values(),
        "STUDY",
    }


def test_each_type_lacking_each_mandatory_key_is_meta_field(meta: ModuleType) -> None:
    for kind, keys in meta.MANDATORY.items():
        meta.check_mandatory(kind, dict.fromkeys(keys, ""), "m")
        for key in keys:
            found = {other: "x" for other in keys if other != key}
            code, message = refusal_of(
                lambda kind=kind, found=found: meta.check_mandatory(kind, found, "meta_x.txt")
            )
            assert (code, message) == ("onco.META_FIELD", f"«meta_x.txt» lacks the key {key}")


def test_a_maf_meta_without_a_profile_name_is_meta_field(
    make_study: Any, discovery: Any, base: Any
) -> None:
    """Every type's keys are checked, not only the clinical types'."""
    base["meta_mutations.txt"] = (
        b"cancer_study_identifier: demo_study\ngenetic_alteration_type: MUTATION_EXTENDED\n"
        b"datatype: MAF\nstable_id: mutations\nshow_profile_in_analysis_tab: true\n"
        b"profile_description: M.\ndata_filename: data_mutations.txt\n"
    )
    refusal = discovery.refusal(make_study(base))
    assert refusal.code == "onco.META_FIELD"
    assert refusal.message[0] == DataSegment(data="meta_mutations.txt")


# --- stable_id (V:4755-4774) ----------------------------------------------------------------------


def test_stable_id_characters(meta: ModuleType) -> None:
    assert meta.stable_id(" mutations ") == "mutations"
    assert meta.stable_id("a.b_c(d)[e]'f,g+h-i:j;K9") == "a.b_c(d)[e]'f,g+h-i:j;K9"
    for bad in ("", " ", "a b", "a/b", "a\tb", "é", "a*b", "a\\b", 'a"b', "a{b}", "a=b", "\u0663"):
        assert meta.stable_id(bad) is None, bad


MAF_META = (
    b"cancer_study_identifier: demo_study\ngenetic_alteration_type: MUTATION_EXTENDED\n"
    b"datatype: MAF\nstable_id: mutations\nshow_profile_in_analysis_tab: true\n"
    b"profile_name: M\nprofile_description: M.\ndata_filename: data_mutations.txt\n"
)


@pytest.mark.parametrize("value", [b"", b" ", b"a b", b"a/b"])
def test_a_stable_id_refused(make_study: Any, discovery: Any, base: Any, value: bytes) -> None:
    base["meta_mutations.txt"] = MAF_META.replace(b"stable_id: mutations", b"stable_id: " + value)
    outcome = discovery.outcome(make_study(base))
    assert outcome[:2] == ("onco.META_VALUE", "meta_mutations.txt")
    assert "stable_id" in outcome[2]


def test_a_repeated_stable_id_is_refused(make_study: Any, discovery: Any, base: Any) -> None:
    base["meta_mutations.txt"] = MAF_META
    base["meta_mutations2.txt"] = MAF_META.replace(
        b"stable_id: mutations", b"stable_id:  mutations"
    )
    outcome = discovery.outcome(make_study(base))
    assert outcome[:2] == ("onco.DUPLICATE_STABLE_ID", "meta_mutations2.txt")
    base["meta_mutations2.txt"] = MAF_META.replace(b"stable_id: mutations", b"stable_id: other")
    assert discovery.outcome(make_study(base))[0] == "ok"


def test_a_clinical_stable_id_is_checked_too(make_study: Any, discovery: Any, base: Any) -> None:
    base["meta_clinical_sample.txt"] += b"stable_id: a b\n"
    assert discovery.outcome(make_study(base))[:2] == (
        "onco.META_VALUE",
        "meta_clinical_sample.txt",
    )


# --- The study meta's values (§5) -----------------------------------------------------------------


def test_the_30_control_characters(text_module: ModuleType) -> None:
    controls = text_module.CONTROLS
    assert len(controls) == 30
    assert controls == {chr(c) for c in range(0x20) if chr(c) not in "\t\n\r"} | {"\x7f"}
    for character in sorted(controls):
        assert text_module.written(f"a{character}b", 10) is None
    for allowed in ("\t", "\x80", "\x85", "\u00a0", "\u2028", "é"):
        assert text_module.written(f"a{allowed}b", 10) == f"a{allowed}b"
    assert text_module.written("a\udcffb", 10) is None
    assert text_module.written("a\ufffeb", 10) is None
    assert text_module.written("", 10) is None
    assert text_module.written("", 10, least=0) == ""
    assert text_module.written("abc", 2) is None


def study_with(base: dict[str, bytes], key: str, value: bytes) -> dict[str, bytes]:
    """The base study, its study meta's ``key`` set to ``value`` (the last line wins)."""
    base["meta_study.txt"] += key.encode() + b": " + value + b"\n"
    return base


def check_value(
    make_study: Any, discovery: Any, base: dict[str, bytes], key: str, value: bytes
) -> str:
    """``ok``, or ``refused`` for ``META_VALUE`` naming ``key``."""
    outcome = discovery.outcome(make_study(study_with(dict(base), key, value)))
    if outcome[0] == "ok":
        return "ok"
    assert outcome[0] == "onco.META_VALUE", outcome
    assert outcome[2].endswith(f" holds a {key} that M4.2a does not accept"), outcome
    return "refused"


BOUNDS = [
    ("name", 255),
    ("description", 1_024),
    ("citation", 200),
    ("groups", 200),
    ("short_name", 64),
    ("pmid", None),
]


@pytest.mark.parametrize(("key", "most"), [bound for bound in BOUNDS if bound[1] is not None])
def test_bound_pairs(make_study: Any, discovery: Any, base: Any, key: str, most: int) -> None:
    def outcome(value: bytes) -> str:
        return check_value(make_study, discovery, base, key, value)

    assert outcome(b"d" * most) == "ok"
    assert outcome(b"d" * (most + 1)) == "refused"
    assert outcome("é".encode() * most) == "ok"
    assert outcome(b"") == "ok"  # empty means no value
    assert outcome(b"d\x1cd") == "refused"
    assert outcome(b"d\x7f") == "refused"


def test_cancer_study_identifier(make_study: Any, discovery: Any, base: Any) -> None:
    def outcome(value: bytes) -> str:
        changed = {name: content.replace(b"demo_study", value) for name, content in base.items()}
        found = discovery.outcome(make_study(changed))
        if found[0] == "ok":
            return "ok"
        assert found[0] == "onco.META_VALUE", found
        assert "cancer_study_identifier" in found[2]
        return "refused"

    assert outcome(b"s" * 200) == "ok"
    assert outcome(b"s" * 201) == "refused"
    assert outcome(b"a_b_0") == "ok"
    for bad in (b"", b"A", b"a-b", b"a b", b"a.b", "\u00e9".encode(), b"a\x1cb"):
        assert outcome(bad) == "refused", bad


def test_type_of_cancer(make_study: Any, discovery: Any, base: Any) -> None:
    def outcome(value: bytes) -> str:
        base["meta_study.txt"] = base["meta_study.txt"].replace(
            b"type_of_cancer: brca", b"type_of_cancer: " + value
        )
        found = discovery.outcome(make_study(base))
        base["meta_study.txt"] = base["meta_study.txt"].replace(
            b"type_of_cancer: " + value + b"\n", b"type_of_cancer: brca\n", 1
        )
        if found[0] == "ok":
            return "ok"
        assert found[:2] == ("onco.META_VALUE", "meta_study.txt"), found
        assert "type_of_cancer" in found[2]
        return "refused"

    assert outcome(b"b" * 32) == "ok"
    assert outcome(b"MDS/MPN") == "ok"
    assert outcome(b"hcl-v_2") == "ok"
    for bad in (b"soft tissue", b"b.c", b"b" * 33, b"", b"br\x0bca", b"b" * 64, "bé".encode()):
        assert outcome(bad) == "refused", bad


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (b"12345", "ok"),
        (b"1,2,999999999", "ok"),
        (b" 7 ", "ok"),
        (b"123, 45", "refused"),
        (b"1,\t2", "refused"),
        (b"", "refused"),
        (b"0", "refused"),
        (b"+3", "refused"),
        (b"-3", "refused"),
        (b"1_0", "refused"),
        ("\u0663".encode(), "refused"),
        ("1\u0663".encode(), "refused"),
        ("\u0661".encode(), "refused"),
        (b"1234567890", "refused"),
        (b"0123", "refused"),
        (b"1,,2", "refused"),
        (b"1 2", "refused"),
        (b"1;2", "refused"),
        (b"1,2;3", "refused"),
        (b"1 ,2", "refused"),
        (b"d" * 1_025, "refused"),
    ],
)
def test_pmid(make_study: Any, discovery: Any, base: Any, value: bytes, expected: str) -> None:
    assert check_value(make_study, discovery, base, "pmid", value) == expected


def test_pmid_of_1024_characters(make_study: Any, discovery: Any, base: Any) -> None:
    value = b",".join([b"123456789"] * 102) + b",1234"
    assert len(value) == 1_024
    assert check_value(make_study, discovery, base, "pmid", value) == "ok"
    assert check_value(make_study, discovery, base, "pmid", value + b"5") == "refused"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (b"hg19", "ok"),
        (b"hg38", "ok"),
        (b"mm10", "ok"),
        (b"hg17", "refused"),
        (b"HG19", "refused"),
        (b"", "refused"),
    ],
)
def test_reference_genome(
    make_study: Any, discovery: Any, base: Any, value: bytes, expected: str
) -> None:
    assert check_value(make_study, discovery, base, "reference_genome", value) == expected


def test_an_empty_citation_means_no_citation(make_study: Any, discovery: Any, base: Any) -> None:
    base["meta_study.txt"] = base["meta_study.txt"].replace(
        b"citation: Demo et al., 2026", b"citation:"
    )
    found = discovery.run(make_study(base))
    assert found.fields.citation is None
    assert found.fields.name == "Demo study"


def test_the_fields_are_kept_as_written(make_study: Any, discovery: Any, base: Any) -> None:
    base["meta_study.txt"] += (
        "groups: A;B\nshort_name: Démo\nreference_genome: hg38\ndescription: 12 samples\u2028x\n"
    ).encode()
    fields = discovery.run(make_study(base)).fields
    assert (fields.cancer_study_identifier, fields.type_of_cancer) == ("demo_study", "brca")
    assert (fields.groups, fields.short_name, fields.reference_genome) == ("A;B", "Démo", "hg38")
    assert fields.description == "12 samples\u2028x"
    assert (fields.citation, fields.pmid) == ("Demo et al., 2026", "12345")


def test_the_study_meta_lacking_name_or_description(
    make_study: Any, discovery: Any, base: Any
) -> None:
    for line in (b"name: Demo study\n", b"description: A synthetic study.\n"):
        changed = dict(base)
        changed["meta_study.txt"] = base["meta_study.txt"].replace(line, b"")
        assert discovery.outcome(make_study(changed))[:2] == ("onco.META_FIELD", "meta_study.txt")


def test_values_are_not_in_the_refusal(make_study: Any, discovery: Any, base: Any) -> None:
    secret = b"Zq9secret"
    base["meta_study.txt"] += b"pmid: " + secret + b"\n"
    refusal = discovery.refusal(make_study(base))
    assert b"zq9secret" not in refusal.model_dump_json().lower().encode()


def test_the_name_cap_is_255_bytes(meta: ModuleType) -> None:
    """P0's cap counts UTF-8 bytes: 255 is within it and 256 is over, for ASCII and for a name
    of two-byte characters."""
    assert meta.file_name("a" * 255) == "a" * 255
    assert meta.file_name("a" * 256) is None
    assert meta.file_name("\u00e9" * 127 + "a") == "\u00e9" * 127 + "a"
    assert meta.file_name("\u00e9" * 128) is None
    assert meta.file_name("a" * 127 + "\u00e9" * 64) == "a" * 127 + "\u00e9" * 64  # 191 characters
    assert meta.file_name("a" * 128 + "\u00e9" * 64) is None


def test_a_path_is_never_built(meta: ModuleType, tmp_path: Path) -> None:
    """``file_name`` reads the value alone: it opens nothing (the names rule is ``study.py``'s)."""
    assert meta.file_name("./data_clinical_sample.txt") == "data_clinical_sample.txt"
    assert meta.file_name(str(tmp_path)) is None


COMPONENTS = [
    "",
    ".",
    "..",
    "a",
    "a.b",
    "..a",
    "a..",
    "\u00e9",
    "x" * 100,
    "x" * 250,
    "a\x00",
    "a b",
]
values = st.one_of(
    st.lists(st.sampled_from(COMPONENTS), max_size=6).map("/".join),
    st.text(alphabet=st.sampled_from(list("./a\x00 \u00e9")), max_size=300),
)


@settings(
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],  # ``meta`` is a module
)
@given(value=values)
def test_p0_refuses_exactly_for_its_seven_causes(meta: ModuleType, value: str) -> None:
    """P0 as the plan states it, written again from the rule and not from ``file_name``: a value is
    refused when it is over 255 bytes, holds a NUL, is absolute, has a ``..`` component, ends in
    ``/`` or ``/.``, or is nothing but empty and ``.`` components (``normpath`` is ``.``); and an
    accepted value's normal form drops its empty and ``.`` components."""
    parts = value.split("/")
    causes = [
        len(value.encode()) > 255,
        "\0" in value,
        value.startswith("/"),
        ".." in parts,
        value.endswith(("/", "/.")),
        all(part in ("", ".") for part in parts),
    ]
    normal = meta.file_name(value)
    assert (normal is None) == any(causes), value
    if normal is not None:
        assert normal == "/".join(part for part in parts if part not in ("", ".")), value
