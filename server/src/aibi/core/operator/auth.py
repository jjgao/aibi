"""The curator token, operator names and CSRF tokens (SPEC §11.2, D261–D263).

The standard library and ``aibi.core.schema`` only, so that the operator CLI can import it
without the server.

**The curator token** is ``aibi_`` and 43 base64url characters: 256 bits from ``secrets``
(``new_token``). Configuration holds only its SHA-256, as ``sha256:<hex>`` (``hash_token``). A
request carries it as ``Authorization: Bearer <token>``; a value that is not in the token's form
is refused before it is hashed, whatever the configuration holds, and the hash is compared in
constant time (``verify``). A fast hash is enough for a secret of 256 bits. A token standing
alone in a URL or a cookie (no base64url character touching either end), as written or
percent-decoded (``holds_token``), refuses the request, so that no client comes to rely on one;
a longer word that holds its shape is a name, as it is in stored text (D265), unless it holds the
configured token itself (``holds_token_of``: each ``aibi_`` window of a token's length is hashed
and compared in constant time). A path written to a log has each segment that holds a token's or
a handle's shape anywhere in it, as written or percent-decoded, blanked (``blank_path``).

**Operator names** travel in one ``Aibi-Operator`` header: the UTF-8 name, percent-encoded with
everything but RFC 3986's unreserved characters encoded (``encode_operator``), and decoded
strictly (``attribution``): a header in any other encoding of the same name is refused, so the
audit trail records one spelling. The name is §5.1's, 1 to 200 characters without control
characters or line breaks, without bidi formatting characters (``BIDI_FORMATTING``), which
could make one name read as another, and holding no token's or handle's shape standing alone
(``SECRET_ALONE_RE``), as written or percent-decoded (``holds_secret``): a swapped variable would
otherwise publish the curator token as a release's ``by``. The server attributes the request to
``operator:<name>``.

**CSRF tokens** are base64url of HMAC-SHA256, keyed by 32 bytes the server draws at start, over
the configured token hash: a new start or a new curator token changes them.

Nothing here writes a token, a hash or a key into a message or a ``repr``.
"""

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
import urllib.parse
from collections.abc import Iterator

from pydantic import TypeAdapter, ValidationError

from aibi.core.schema.descriptors import By
from aibi.core.schema.refusals import (
    SECRET_BLANK,
    SECRET_DECODINGS,
    SECRET_RE,
    holds_secret,
    holds_token_of,
)

OPERATOR_PREFIX = "/operator"
"""The operator router's path; every path at or below it needs the curator token."""
TOKEN_PREFIX = "aibi_"
TOKEN_RE = re.compile(r"^aibi_[A-Za-z0-9_-]{43}$")
TOKEN_ANYWHERE = re.compile(rb"(?<![A-Za-z0-9_-])aibi_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")
"""A token standing alone anywhere in a URL or a cookie, as bytes."""
SECRET_ANYWHERE = re.compile(SECRET_RE.pattern.encode("ascii"))
"""A token or a session handle, inside a longer word too, as bytes."""
DECODINGS = SECRET_DECODINGS
"""How many times a URL is percent-decoded when a secret is looked for in it: a server decodes a
path once, and a proxy or a log may once more."""
BIDI_FORMATTING = frozenset(
    chr(point) for point in (0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A))
)
"""The bidi formatting characters: the Arabic letter mark, the marks, the embeddings and
overrides, and the isolates."""
TOKEN_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
OPERATOR_HEADER = "aibi-operator"
CSRF_HEADER = "aibi-csrf"
AUTHORIZATION = 'Bearer realm="aibi-operator"'
"""The ``WWW-Authenticate`` challenge of a request without the token."""
OPERATOR_KEY = "aibi.operator"
"""The ASGI scope key under which request protection puts the request's attribution."""
CSRF_KEY = "aibi.csrf"
"""The ASGI scope key under which request protection puts the CSRF token of the process."""

_CSRF_CONTEXT = b"aibi-csrf/1\0"
_ENCODED_NAME = re.compile(r"^(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2})+$")
_BY: TypeAdapter[str] = TypeAdapter(By)


def new_token() -> str:
    """A new curator token."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def is_token(value: str) -> bool:
    """Whether ``value`` is in the curator token's form."""
    return TOKEN_RE.fullmatch(value) is not None


def hash_token(token: str) -> str:
    """The token's hash, as configuration holds it: ``sha256:<hex>``. Refuses (``ValueError``) a
    value that is not in the token's form, without quoting it."""
    if not is_token(token):
        raise ValueError("not a curator token: aibi_ and 43 base64url characters")
    return "sha256:" + hashlib.sha256(token.encode("ascii")).hexdigest()


def token_digest(configured: str) -> bytes:
    """The 32 bytes of a configured hash, ``sha256:<hex>``."""
    if TOKEN_HASH_RE.fullmatch(configured) is None:
        raise ValueError("not a token hash: sha256: and 64 lowercase hexadecimal digits")
    return bytes.fromhex(configured.removeprefix("sha256:"))


def bearer(authorization: str) -> str | None:
    """The token of an ``Authorization`` value ``Bearer <token>``, the scheme in any case; ``None``
    for any other scheme, or a value not in the token's form."""
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = credentials.strip(" ")
    return token if is_token(token) else None


def verify(token: str, digest: bytes) -> bool:
    """Whether a token in form hashes to ``digest``, compared in constant time."""
    return is_token(token) and hmac.compare_digest(
        hashlib.sha256(token.encode("ascii")).digest(), digest
    )


def csrf_token(key: bytes, digest: bytes) -> str:
    """The CSRF token of a process's key and the configured token hash (D263)."""
    mac = hmac.new(key, _CSRF_CONTEXT + digest, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def csrf_ok(given: str | None, key: bytes, digest: bytes) -> bool:
    """Whether ``given`` is the CSRF token, compared in constant time."""
    if given is None:
        return False
    expected = csrf_token(key, digest).encode("ascii")
    return hmac.compare_digest(given.encode("utf-8", "surrogateescape"), expected)


def _decodings(value: bytes) -> Iterator[bytes]:
    yield value
    for _ in range(DECODINGS):
        decoded = urllib.parse.unquote_to_bytes(value)
        if decoded == value:
            return
        value = decoded
        yield value


def holds_token(value: bytes, digest: bytes | None = None) -> bool:
    """Whether a URL's path and query, or a cookie, hold a curator token standing alone, as
    written or percent-decoded up to ``DECODINGS`` times, or, given the configured hash's
    ``digest``, the configured token anywhere, inside a longer word too (``holds_token_of``)."""
    if any(TOKEN_ANYWHERE.search(found) for found in _decodings(value)):
        return True
    return digest is not None and holds_token_of(value.decode("latin-1"), digest)


def blank_path(path: str) -> str:
    """``path`` with each segment that holds a token's or a handle's shape, as written or
    percent-decoded, replaced by ``SECRET_BLANK``: the form a path is logged in (D261, D267)."""

    def blanked(segment: str) -> str:
        written = segment.encode("utf-8", "surrogateescape")
        found = any(SECRET_ANYWHERE.search(value) for value in _decodings(written))
        return SECRET_BLANK if found else segment

    return "/".join(blanked(segment) for segment in path.split("/"))


def valid_name(name: str) -> bool:
    """Whether ``name`` is an operator's self-declared name (§5.1), without bidi formatting and
    without a token's or a handle's shape standing alone in it, as written or percent-decoded
    (D262)."""
    if not BIDI_FORMATTING.isdisjoint(name) or holds_secret(name):
        return False
    try:
        _BY.validate_python(f"operator:{name}")
    except ValidationError:
        return False
    return True


def encode_operator(name: str) -> str:
    """A name as the ``Aibi-Operator`` header carries it."""
    return urllib.parse.quote(name, safe="")


def attribution(header: str) -> str | None:
    """The attribution, ``operator:<name>``, of an ``Aibi-Operator`` value; ``None`` if it is
    not a name encoded as ``encode_operator`` encodes one, or not a valid name."""
    if _ENCODED_NAME.fullmatch(header) is None:
        return None
    try:
        name = urllib.parse.unquote(header, errors="strict")
    except UnicodeDecodeError:
        return None
    if encode_operator(name) != header or not valid_name(name):
        return None
    return f"operator:{name}"


def is_loopback_host(host: str) -> bool:
    """Whether a host names the loopback interface: ``localhost``, 127.0.0.0/8 or ``::1``, an IPv6
    literal with or without its brackets."""
    if host.lower() == "localhost":
        return True
    literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        return ipaddress.ip_address(literal).is_loopback
    except ValueError:
        return False


__all__ = [
    "AUTHORIZATION",
    "BIDI_FORMATTING",
    "CSRF_HEADER",
    "CSRF_KEY",
    "DECODINGS",
    "OPERATOR_HEADER",
    "OPERATOR_KEY",
    "OPERATOR_PREFIX",
    "SECRET_ANYWHERE",
    "TOKEN_ANYWHERE",
    "TOKEN_HASH_RE",
    "TOKEN_PREFIX",
    "TOKEN_RE",
    "attribution",
    "bearer",
    "blank_path",
    "csrf_ok",
    "csrf_token",
    "encode_operator",
    "hash_token",
    "holds_token",
    "is_loopback_host",
    "is_token",
    "new_token",
    "token_digest",
    "valid_name",
    "verify",
]
