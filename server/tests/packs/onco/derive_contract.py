"""Derive the constants ``test_contract.py`` checks in from their sources, and report differences.

This is not a test: pytest does not collect it (its name does not start with ``test_``) and CI does
not run it. It reads two files that are not in the repository, the Sequence Ontology's ``so.obo``
and vcf2maf's ``vcf2maf.pl``, so a re-verification against a new release of either is a command::

    cd server
    uv run python tests/packs/onco/derive_contract.py PATH/TO/so.obo PATH/TO/vcf2maf.pl

It exits 0 and prints the sizes if every constant is what the sources give, and exits 1 and prints
each difference if not. What it checks, independently of the test's own code:

- ``CITED``: each code names the term given in ``so.obo``, and none is obsolete;
- ``DOWN``: each cited term's descendants, all the way down;
- ``SO_IS_A``: its keys are the cited terms, their descendants and the terms a class holds, with
  all their ancestors, and each term's parents are ``so.obo``'s ``is_a`` edges;
- ``VCF2MAF``: the terms ``GetVariantClassification`` files in each class, by its regular
  expressions (a suffix expands to every name in ``so.obo`` that ends with it);
- every ``# name`` comment beside a code in ``test_contract.py`` is that code's name.

The names ``vcf2maf.pl`` uses that ``so.obo`` has no term of are reported and left out, as
``VCF2MAF``'s docstring says. ``FALLBACKS`` and the relations are judgements, and are not derived
here.
"""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

TEST = Path(__file__).with_name("test_contract.py")


def read_obo(path: Path) -> tuple[dict[str, str], set[str], dict[str, list[str]]]:
    """Each term's name, the obsolete ones, and each term's ``is_a`` parents."""
    names: dict[str, str] = {}
    obsolete: set[str] = set()
    parents: dict[str, list[str]] = {}
    current: dict[str, str] = {}
    kind = ""
    stanza_parents: list[str] = []

    def close() -> None:
        if kind == "[Term]" and "id" in current:
            names[current["id"]] = current.get("name", "")
            parents[current["id"]] = stanza_parents[:]
            if current.get("is_obsolete") == "true":
                obsolete.add(current["id"])

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("["):
            close()
            kind, current, stanza_parents = line, {}, []
        elif ": " in line:
            key, _, value = line.partition(": ")
            if key == "is_a":
                stanza_parents.append(value.split(" ")[0])
            elif key not in current:
                current[key] = value
    close()
    return names, obsolete, parents


def above(parents: dict[str, list[str]], term: str) -> set[str]:
    found: set[str] = set()
    pending = [term]
    while pending:
        for parent in parents[pending.pop()]:
            if parent not in found:
                found.add(parent)
                pending.append(parent)
    return found


def below(children: dict[str, set[str]], term: str) -> set[str]:
    found: set[str] = set()
    pending = [term]
    while pending:
        for child in children.get(pending.pop(), ()):
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def read_vcf2maf(path: Path, ids: dict[str, list[str]]) -> tuple[dict[str, list[str]], list[str]]:
    """The terms ``GetVariantClassification`` files in each class, and the names without a term."""
    source = path.read_text(encoding="utf-8")
    body = source[
        source.index("sub GetVariantClassification") : source.index("sub FixAlleleDepths")
    ]
    derived: dict[str, list[str]] = {}
    unknown: list[str] = []
    for match in re.finditer(r'return "([^"]+)" if\s*\((.*)\);', body):
        value, condition = match.group(1), match.group(2)
        names = re.findall(r"\$effect eq '([^']+)'", condition)
        for group in re.findall(r"\$effect =~ /\^\(([^)]*)\)\$/", condition):
            names += group.split("|")
        for suffix in re.findall(r"\$effect =~ /([a-z_]+)\$/", condition):
            names += [name for name in ids if name.endswith(suffix)]
        found: list[str] = []
        for name in names:
            if name not in ids:
                unknown.append(f"{value}: {name}")
                continue
            found += [code for code in ids[name] if code not in found]
        if value != "Targeted_Region":
            derived[value] = found
    return derived, unknown


def load_test() -> ModuleType:
    spec = importlib.util.spec_from_file_location("test_contract_constants", TEST)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def differences(obo: Path, perl: Path) -> tuple[list[str], str]:
    constants = load_test()
    names, obsolete, parents = read_obo(obo)
    ids: dict[str, list[str]] = {}
    for code, name in names.items():
        if code not in obsolete:
            ids.setdefault(name, []).append(code)
    children: dict[str, set[str]] = {}
    for code, given in parents.items():
        for parent in given:
            children.setdefault(parent, set()).add(code)
    bad: list[str] = []

    for value, (code, name, _) in constants.CITED.items():
        if names.get(code) != name or code in obsolete:
            bad.append(f"CITED {value}: {code} is {names.get(code)!r}, not {name!r}")

    for code, listed in constants.DOWN.items():
        derived = below(children, code)
        if set(listed) != derived or len(listed) != len(set(listed)):
            bad.append(
                f"DOWN {code}: missing {sorted(derived - set(listed))}, "
                f"extra {sorted(set(listed) - derived)}"
            )
    if set(constants.DOWN) != {code for code, _, _ in constants.CITED.values()}:
        bad.append("DOWN: its terms are not the cited terms")

    derived_vcf, unknown = read_vcf2maf(perl, ids)
    for value in sorted(set(derived_vcf) | set(constants.VCF2MAF)):
        first, second = set(derived_vcf.get(value, ())), set(constants.VCF2MAF.get(value, ()))
        if first != second:
            bad.append(
                f"VCF2MAF {value}: derived only {sorted(first - second)}, "
                f"checked in only {sorted(second - first)}"
            )

    needed = set(constants.DOWN) | {code for below_ in constants.DOWN.values() for code in below_}
    needed |= {code for terms in constants.VCF2MAF.values() for code in terms}
    closure = set(needed)
    for code in needed:
        closure |= above(parents, code)
    if set(constants.SO_IS_A) != closure:
        bad.append(
            f"SO_IS_A keys: missing {sorted(closure - set(constants.SO_IS_A))}, "
            f"extra {sorted(set(constants.SO_IS_A) - closure)}"
        )
    for code, listed in constants.SO_IS_A.items():
        if set(listed) != set(parents.get(code, ())):
            bad.append(f"SO_IS_A {code}: {listed} is not {parents.get(code)}")

    for match in re.finditer(r'"(SO:\d{7})"[^#\n]*#\s*(\S+)', TEST.read_text(encoding="utf-8")):
        if names.get(match.group(1)) != match.group(2):
            bad.append(
                f"comment {match.group(1)}: {match.group(2)}, not {names.get(match.group(1))}"
            )

    note = f"SO_IS_A {len(constants.SO_IS_A)}, DOWN {len(constants.DOWN)}"
    if unknown:
        note += f"; names without a term in so.obo, left out: {', '.join(unknown)}"
    return bad, note


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    bad, note = differences(Path(argv[1]), Path(argv[2]))
    print("\n".join(bad))
    print(f"{len(bad)} differences; {note}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
