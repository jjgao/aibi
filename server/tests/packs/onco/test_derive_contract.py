"""``derive_contract.py``'s reading of ``GetVariantClassification``, on small synthetic Perl.

The script is run by hand against the real ``vcf2maf.pl`` (it is not run in CI, and the file is not
in the repository), but its reading of the rules is the part that can be wrong in a way no other
test sees: it derives ``VCF2MAF``, which ``test_contract.py`` takes as given. So ``read_vcf2maf``
is pure, and each way a rule can change what another gives is held here on text that is not
vcf2maf's: a shadowed rule, a name taken by an earlier rule, a class with a second rule, and a
rule that may not fire. The module imports no pack.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).with_name("derive_contract.py")

IDS = {
    "alpha": ["SO:1"],
    "beta": ["SO:2"],
    "gamma": ["SO:3"],
    "delta": ["SO:4"],
    "inframe_alpha": ["SO:5"],
    "short_alpha": ["SO:6"],
}


def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("derive_contract_script", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def perl(*rules: str) -> str:
    """A ``vcf2maf.pl`` with these rules in ``GetVariantClassification``."""
    lines = "\n".join(f"    {rule}" for rule in rules)
    return (
        "sub GetVariantClassification {\n"
        "    my ( $effect, $var_type, $inframe ) = @_;\n"
        '    return "Targeted_Region" if( not defined $effect or not $effect );\n'
        f"{lines}\n"
        "}\n\n"
        "sub FixAlleleDepths {\n"
        "    return \"Other\" if( $effect eq 'delta' );\n"
        "}\n"
    )


def read(*rules: str) -> tuple[dict[str, list[str]], list[str]]:
    return script().read_vcf2maf(perl(*rules), IDS)


def test_a_rule_gives_its_names_and_the_targeted_region_rule_is_left_out() -> None:
    derived, unknown = read(
        "return \"One\" if( $effect eq 'alpha' );",
        'return "Two" if( $effect =~ /^(beta|gamma)$/ );',
    )
    assert derived == {"One": ["SO:1"], "Two": ["SO:2", "SO:3"]}
    assert unknown == []


def test_a_name_without_a_term_is_reported_and_left_out() -> None:
    derived, unknown = read("return \"One\" if( $effect eq 'alpha' or $effect eq 'nothing' );")
    assert derived == {"One": ["SO:1"]}
    assert unknown == ["One: nothing"]


def test_a_suffix_expands_to_every_name_that_ends_with_it() -> None:
    derived, _ = read('return "One" if( $effect =~ /alpha$/ );')
    assert derived == {"One": ["SO:1", "SO:5", "SO:6"]}


def test_a_shadowed_rule_gives_nothing() -> None:
    derived, _ = read(
        "return \"One\" if( $effect eq 'alpha' );",
        "return \"Two\" if( $effect eq 'alpha' );",
        "return \"Two\" if( $effect eq 'beta' );",
    )
    assert derived == {"One": ["SO:1"], "Two": ["SO:2"]}


def test_a_name_an_earlier_rule_takes_is_not_the_later_rules() -> None:
    derived, _ = read(
        "return \"One\" if( $effect eq 'alpha' );",
        'return "Two" if( $effect =~ /alpha$/ );',
        'return "Three" if( $effect =~ /^(beta|alpha)$/ );',
    )
    assert derived == {"One": ["SO:1"], "Two": ["SO:5", "SO:6"], "Three": ["SO:2"]}


def test_a_class_with_a_second_rule_holds_the_terms_of_both() -> None:
    derived, _ = read(
        "return \"One\" if( $effect eq 'alpha' );",
        "return \"Two\" if( $effect eq 'beta' );",
        "return \"One\" if( $effect eq 'gamma' );",
        "return \"One\" if( $effect eq 'alpha' or $effect eq 'delta' );",
    )
    assert derived == {"One": ["SO:1", "SO:3", "SO:4"], "Two": ["SO:2"]}


def test_a_rule_that_may_not_fire_takes_no_name_from_the_rules_after_it() -> None:
    derived, _ = read(
        "return \"One\" if( $effect eq 'alpha' and $var_type eq 'DEL' );",
        "return \"Two\" if( $effect eq 'beta' and $inframe );",
        'return "Three" if( $effect =~ /^(alpha|beta)$/ );',
    )
    assert derived == {"One": ["SO:1"], "Two": ["SO:2"], "Three": ["SO:1", "SO:2"]}


def test_only_the_classification_subroutine_is_read() -> None:
    derived, _ = read("return \"One\" if( $effect eq 'alpha' );")
    assert "Other" not in derived
