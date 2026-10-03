"""The oncology pack's ontology systems (SPEC §5.4, §10.1): offline, syntax-only validators.

An installed pack's systems are checked in every dataset of the deployment, whatever its
``packs`` lists, on the codes of a dataset's ``data_use`` and of columns' and permissible values'
``concepts`` (D247). Those codes come from importers, operators and agents, so they are
untrusted text. Each validator is total by construction: it reads its argument only if it is
exactly a ``str`` (a subclass is refused before anything of it is read), refuses it past its
form's own length before it reads a character, and then compares characters with constants. Each
returns exactly ``True`` or ``False``. There is no ``try``: nothing on an exact ``str`` can raise
here, and an exception that does arise (``MemoryError``, ``KeyboardInterrupt``) passes, as D388
expects of a hook.

The forms, compared as written (no case folding, no trimming):

- ``OncoTree``: 1 to 32 characters of ``A-Z``, ``a-z``, ``0-9``, ``_``, ``-`` and ``/``
  (``MDS/MPN``, ``HCL-V``; cBioPortal writes cancer types in lower case). The canonical form is
  upper case.
- ``HGNC``: ``HGNC:<n>`` or ``<n>``, where ``<n>`` is 1 to 7 digits with no leading zero, and not
  ``0``. A symbol (``TP53``) is refused: it is neither unique over time nor stable, and the HGNC
  id is the gene's key. The canonical form is ``HGNC:<n>``.
- ``NCBIGene``: ``NCBIGene:<n>`` or ``<n>``, where ``<n>`` is 1 to 10 digits with no leading zero,
  and not ``0``; a negative id (cBioPortal's internal stand-in) is refused. The canonical form is
  ``<n>``.
- ``SO``: ``SO:``, ``SO_`` (the OBO form) or nothing, then exactly 7 digits. The canonical form is
  ``SO:<7 digits>``.
"""

from aibi.core.schema.pack_api import OntologyValidator

DIGITS = frozenset("0123456789")
"""The ASCII digits, the only digits a code may hold."""
LEADING = frozenset("123456789")
"""The digits a number may start with."""
ONCOTREE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-/")
"""The characters of an OncoTree code."""

ONCOTREE_MOST = 32
HGNC_PREFIX = "HGNC:"
HGNC_DIGITS = 7
HGNC_MOST = len(HGNC_PREFIX) + HGNC_DIGITS
NCBIGENE_PREFIX = "NCBIGene:"
NCBIGENE_DIGITS = 10
NCBIGENE_MOST = len(NCBIGENE_PREFIX) + NCBIGENE_DIGITS
SO_PREFIXES = ("SO:", "SO_")
SO_DIGITS = 7
SO_MOST = 3 + SO_DIGITS


def _number(text: str, most: int) -> bool:
    """Whether ``text`` is 1 to ``most`` ASCII digits, the first not ``0``."""
    return (
        1 <= len(text) <= most
        and text[0] in LEADING
        and all(character in DIGITS for character in text)
    )


def oncotree(code: str) -> bool:
    """Whether ``code`` is an OncoTree code by its syntax."""
    if type(code) is not str or len(code) > ONCOTREE_MOST:
        return False
    return len(code) >= 1 and all(character in ONCOTREE for character in code)


def hgnc(code: str) -> bool:
    """Whether ``code`` is an HGNC id by its syntax: ``HGNC:<n>`` or ``<n>``."""
    if type(code) is not str or len(code) > HGNC_MOST:
        return False
    if code.startswith(HGNC_PREFIX):
        return _number(code[len(HGNC_PREFIX) :], HGNC_DIGITS)
    return _number(code, HGNC_DIGITS)


def ncbigene(code: str) -> bool:
    """Whether ``code`` is an NCBI Gene id by its syntax: ``NCBIGene:<n>`` or ``<n>``."""
    if type(code) is not str or len(code) > NCBIGENE_MOST:
        return False
    if code.startswith(NCBIGENE_PREFIX):
        return _number(code[len(NCBIGENE_PREFIX) :], NCBIGENE_DIGITS)
    return _number(code, NCBIGENE_DIGITS)


def so(code: str) -> bool:
    """Whether ``code`` is a Sequence Ontology id by its syntax: ``SO:``, ``SO_`` or nothing,
    then 7 digits."""
    if type(code) is not str or len(code) > SO_MOST:
        return False
    digits = code[3:] if code.startswith(SO_PREFIXES) else code
    return len(digits) == SO_DIGITS and all(character in DIGITS for character in digits)


SYSTEMS: dict[str, OntologyValidator] = {
    "OncoTree": oncotree,
    "HGNC": hgnc,
    "NCBIGene": ncbigene,
    "SO": so,
}
"""The pack's ontology systems, by their canonical, case-sensitive names."""
