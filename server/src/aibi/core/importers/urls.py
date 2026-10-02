"""A server connection's URL, read by the server's own grammar, and the connection string built
from what it reads (SPEC §14, D305, D310).

A Postgres or MySQL connection's URL is never handed to a driver. ``parse`` reads it by one
strict grammar and refuses whatever lies outside it (``UrlError``, whose message holds no text
of the URL but a parameter's name):

``<scheme>://<user>[:<password>]@<host>[:<port>]/<database>[?<name>=<value>[&…]]``

- the scheme is ``postgresql`` or ``postgres`` (Postgres), or ``mysql``, written in lower case;
- the URL is printable ASCII without a space or a ``#``;
- the authority holds exactly one ``@``; the user is not empty; the user, the password, the
  database and the parameters' values are percent-decoded, as UTF-8 without a NUL (a ``+`` is
  itself);
- the host is one DNS name, or one IPv4 address in dotted decimal (a host whose last label is all
  digits or begins with ``0x`` is one, which ``getaddrinfo`` would otherwise read in octal, in
  hexadecimal or in short), or one bracketed IPv6 address, without a ``,`` or a percent-escape,
  recorded in its canonical form (RFC 5952's, an IPv4-mapped address's last 32 bits in dotted
  decimal whatever Python's version writes); the port, if given, is a number from 1 to 65,535;
- the database is one path segment, holding no ``/`` and no control character once decoded (as
  the configuration's schema holds none), and no longer than the server keeps a name (Postgres:
  63 bytes of UTF-8, as a Postgres user is too; MySQL: 64 characters), since Postgres cuts a
  longer name short and opens the database the shorter one names;
- for MySQL, no value holds a ``?`` and the host is not ``localhost`` unless ``socket`` is given
  (``_mysql_checked``);
- a MySQL database holds only what DuckDB's MySQL scanner quotes safely (``mysql_quotable``: no
  backtick, backslash or ``'``, no control character);
- each parameter's name is one the kind allows (``PARAMETERS``: TLS and timeouts, never another
  host, database, service or option file), given once.

``ServerUrl.connection`` then builds the connection string the driver reads, in its
``keyword=value`` form, from exactly what was read, each value quoted and escaped (libpq's single
quotes; the MySQL scanner's double quotes), a Postgres one with ``POSTGRES_OPTIONS`` too, and
``ServerUrl.host`` and ``ServerUrl.database`` are what the provenance records. The snapshot then
reads back the database the server opened, and refuses one other than this (``snapshot``, D310).
"""

import ipaddress
import re
import string
import unicodedata
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

ServerKind = Literal["postgres", "mysql"]
SCHEMES: Mapping[ServerKind, tuple[str, ...]] = {
    "postgres": ("postgresql", "postgres"),
    "mysql": ("mysql",),
}
"""The URL schemes a server's kind takes, in lower case as the drivers read them."""
PARAMETERS: Mapping[ServerKind, frozenset[str]] = {
    "postgres": frozenset(
        {
            "application_name",
            "channel_binding",
            "connect_timeout",
            "gssencmode",
            "keepalives",
            "keepalives_count",
            "keepalives_idle",
            "keepalives_interval",
            "sslcert",
            "sslcrl",
            "sslkey",
            "sslmode",
            "sslrootcert",
            "tcp_user_timeout",
        }
    ),
    "mysql": frozenset(
        {
            "socket",
            "ssl_ca",
            "ssl_capath",
            "ssl_cert",
            "ssl_cipher",
            "ssl_crl",
            "ssl_crlpath",
            "ssl_key",
            "ssl_mode",
        }
    ),
}
"""The parameters a URL's query may set, by kind: none names another host, database, service or
option file (D310)."""
_PERCENT = r"%[0-9A-Fa-f]{2}"
_USER = rf"(?:[A-Za-z0-9\-._~!$&'()*+,;=]|{_PERCENT})+"
_PASSWORD = rf"(?:[A-Za-z0-9\-._~!$&'()*+,;=:]|{_PERCENT})*"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
_HOST = rf"{_LABEL}(?:\.{_LABEL})*|\[[0-9A-Fa-f:.]+\]"
_SEGMENT = rf"(?:[A-Za-z0-9\-._~!$&'()*+,;=:]|{_PERCENT})+"
_URL = re.compile(
    rf"(?P<scheme>[a-z]+)://(?P<user>{_USER})(?::(?P<password>{_PASSWORD}))?@(?P<host>{_HOST})"
    rf"(?::(?P<port>[0-9]{{1,5}}))?/(?P<database>{_SEGMENT})(?:\?(?P<query>[^#]*))?"
)
_NAME = re.compile(r"[a-z_]+")
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f]")
_NUMERIC = re.compile(r"[0-9]+|0[xX][0-9A-Za-z-]*")
POSTGRES_NAME_BYTES = 63
"""The bytes of UTF-8 Postgres keeps of a database's or a user's name (``NAMEDATALEN - 1``)."""
POSTGRES_OPTIONS = (
    "-c search_path=pg_catalog,pg_temp -c row_security=off -c DateStyle=ISO,YMD "
    "-c IntervalStyle=postgres -c TimeZone=UTC -c client_encoding=UTF8 "
    "-c extra_float_digits=3 -c bytea_output=hex -c lc_numeric=C -c lc_monetary=C "
    "-c quote_all_identifiers=off"
)
"""The settings a Postgres connection is opened with (D309, D310), each ``-c`` a startup option
that overrides whatever the role or the database sets: names resolve in ``pg_catalog`` alone,
which only a superuser can change, so that whoever can write the imported schema cannot change
what the snapshot's statements call; a read that row security would filter fails, rather than
return the rows a policy of the table's owner chooses; and every setting that shapes a value's
text or binary form is fixed, so that a role or database cannot steer the recorded bytes (a
``daterange`` or an ``interval``, which the scanner serialises as text, took its form from
``DateStyle``, ``IntervalStyle`` and ``TimeZone``; ``lc_numeric`` and ``lc_monetary`` shape a
formatted number; ``client_encoding`` its bytes; ``extra_float_digits`` and ``bytea_output`` the
few remaining text forms; ``quote_all_identifiers`` the text of a ``regclass`` or another name's
type), so that an unchanged database re-imports the same (D306)."""
MYSQL_NAME_CHARACTERS = 64
"""The characters of a MySQL database's name."""
_MYSQL_ASCII = frozenset(string.ascii_letters + string.digits + " !\"#$%&'()*+,-./:;<=>?@[]^_{|}~")
"""The ASCII characters DuckDB's MySQL scanner writes safely into the statements it sends: all
printable ones but the backtick, which it escapes in an identifier with a backslash that MySQL
does not read as an escape there, so that the rest of the name is SQL, and the backslash, which
it leaves unescaped in a string constant, where MySQL reads it as an escape (a database ``d\\b`` was
read as empty), and whose tables it fails to read."""


def mysql_quotable(name: str, constant: bool = False) -> bool:
    """Whether DuckDB's MySQL scanner can write ``name`` into the statements it sends the
    server without its text becoming SQL (D308): a name of the characters of ``_MYSQL_ASCII``,
    and of characters beyond ASCII of no control, format, surrogate, private-use or unassigned
    category, which the session's ``utf8mb4`` cannot make syntax of. A name the scanner also
    writes as a string constant (``constant``: the database, which it filters the catalogue by)
    holds no ``'`` either, which it escapes with a backslash in one statement and by doubling it
    in another, the first of which a server whose ``sql_mode`` has ``NO_BACKSLASH_ESCAPES`` reads
    as the quote's end."""
    for character in name:
        if character.isascii():
            if character not in _MYSQL_ASCII or (constant and character == "'"):
                return False
        elif unicodedata.category(character).startswith("C"):
            return False
    return True


class UrlError(ValueError):
    """A URL outside the grammar: the message says why, and holds none of the URL's text but a
    parameter's name."""


@dataclass(frozen=True)
class ServerUrl:
    """What a server connection's URL names, as read by ``parse``."""

    kind: ServerKind
    host: str
    """As the provenance records it: a DNS name in lower case, or an address."""
    port: int | None
    database: str
    user: str = field(repr=False)
    password: str | None = field(repr=False)
    parameters: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    def connection(self) -> str:
        """The driver's connection string, built from what was read alone: never a repr, log
        or message, since it holds the password."""
        quote = _libpq if self.kind == "postgres" else _mysql
        named: list[tuple[str, str]] = [("host", self.host)]
        if self.kind == "postgres":
            named.append(("options", POSTGRES_OPTIONS))
        if self.port is not None:
            named.append(("port", str(self.port)))
        named.append(("dbname" if self.kind == "postgres" else "database", self.database))
        named.append(("user", self.user))
        if self.password is not None:
            named.append(("password", self.password))
        named.extend(self.parameters)
        return " ".join(f"{key}={quote(value)}" for key, value in named)


def _libpq(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _mysql(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _decoded(text: str, what: str) -> str:
    if _BAD_PERCENT.search(text):
        raise UrlError(f"holds a URL whose {what} has a % that begins no percent-escape")
    try:
        found = urllib.parse.unquote(text, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raise UrlError(f"holds a URL whose {what} has percent-escapes that are not UTF-8") from None
    if "\x00" in found:
        raise UrlError(f"holds a URL whose {what} has a NUL character")
    return found


def _host(written: str) -> str:
    if written.startswith("["):
        try:
            address = ipaddress.IPv6Address(written[1:-1])
        except ValueError:
            raise UrlError("holds a URL whose host is not an IPv6 address") from None
        if address.ipv4_mapped is not None:
            return f"::ffff:{address.ipv4_mapped}"
        return str(address)
    if len(written) > 253:
        raise UrlError("holds a URL whose host is longer than a DNS name")
    if _NUMERIC.fullmatch(written.rpartition(".")[2]):
        try:
            return str(ipaddress.IPv4Address(written))
        except ValueError:
            raise UrlError(
                "holds a URL whose host is neither a DNS name nor an IPv4 address in dotted decimal"
            ) from None
    return written.lower()


def _database(kind: ServerKind, written: str) -> str:
    database = _decoded(written, "database")
    if "/" in database:
        raise UrlError("holds a URL whose database, decoded, holds a /")
    if _CONTROL.search(database):
        raise UrlError("holds a URL whose database, decoded, holds a control character")
    if kind == "postgres" and len(database.encode("utf-8")) > POSTGRES_NAME_BYTES:
        raise UrlError(
            f"holds a URL whose database is longer than the {POSTGRES_NAME_BYTES} bytes of UTF-8 "
            "Postgres keeps of a name"
        )
    if kind == "mysql" and len(database) > MYSQL_NAME_CHARACTERS:
        raise UrlError(
            f"holds a URL whose database is longer than MySQL's {MYSQL_NAME_CHARACTERS} characters"
        )
    if kind == "mysql" and not mysql_quotable(database, constant=True):
        raise UrlError(
            "holds a URL whose database has a character DuckDB's MySQL scanner cannot quote safely "
            "(a backtick, a backslash or a ')"
        )
    return database


def _user(kind: ServerKind, written: str) -> str:
    user = _decoded(written, "user")
    if kind == "postgres" and len(user.encode("utf-8")) > POSTGRES_NAME_BYTES:
        raise UrlError(
            f"holds a URL whose user is longer than the {POSTGRES_NAME_BYTES} bytes of UTF-8 "
            "Postgres keeps of a name"
        )
    return user


def _parameters(kind: ServerKind, query: str) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    for part in query.split("&"):
        name, equals, value = part.partition("=")
        if not _NAME.fullmatch(name):
            raise UrlError("holds a URL whose query has a parameter it does not name plainly")
        if not equals:
            raise UrlError(f"holds a URL whose query gives {name} no value")
        if name not in PARAMETERS[kind]:
            raise UrlError(
                f"holds a URL whose query sets {name}, which the server does not read from a URL "
                f"(it reads {', '.join(sorted(PARAMETERS[kind]))})"
            )
        if any(name == seen for seen, _ in found):
            raise UrlError(f"holds a URL whose query sets {name} twice")
        found.append((name, _decoded(value, f"parameter {name}")))
    return tuple(found)


def parse(kind: ServerKind, url: str) -> ServerUrl:
    """What ``url`` names, by the grammar of the module's docstring. Raises ``UrlError``."""
    schemes = SCHEMES[kind]
    shape = f"{schemes[0]}://user[:password]@host[:port]/database[?name=value&…]"
    if not url.isascii() or not url.isprintable() or " " in url:
        raise UrlError("holds a URL with a character a URL does not hold: percent-encode it")
    if "#" in url:
        raise UrlError(
            "holds a URL with a #, which the drivers do not read as a fragment: write it as %23"
        )
    scheme, separator, rest = url.partition("://")
    if not separator or scheme not in schemes:
        raise UrlError(f"holds no {'://… or '.join(schemes)}://… URL: write {shape}")
    authority = re.split(r"[/?]", rest, maxsplit=1)[0]
    if authority.count("@") != 1:
        raise UrlError("holds a URL whose authority does not have exactly one @")
    host = authority.partition("@")[2]
    if "," in host or "%" in host:
        raise UrlError("holds a URL whose host is not one name or address, unescaped")
    matched = _URL.fullmatch(url)
    if matched is None:
        raise UrlError(f"holds a URL outside the grammar the server reads: write {shape}")
    port = matched["port"]
    if port is not None and not 0 < int(port) < 65536:
        raise UrlError("holds a URL whose port is not from 1 to 65535")
    database = _database(kind, matched["database"])
    password = matched["password"]
    query = matched["query"]
    read = ServerUrl(
        kind,
        _host(matched["host"]),
        None if port is None else int(port),
        database,
        _user(kind, matched["user"]),
        None if password is None else _decoded(password, "password"),
        () if query is None else _parameters(kind, query),
    )
    if kind == "mysql":
        _mysql_checked(read)
    return read


def _mysql_checked(read: ServerUrl) -> None:
    """Refuse what the MySQL scanner would read otherwise than written (D310): a ``?`` in any
    value, where it splits the whole connection string into attributes whatever the quoting, and
    the host ``localhost`` without ``socket``, which its client reads as its compiled-in default
    socket (``/tmp/mysql.sock``), a path any local user can plant, rather than as TCP."""
    parts = [("user", read.user), ("password", read.password or ""), ("database", read.database)]
    parts.extend((f"parameter {name}", value) for name, value in read.parameters)
    for what, value in parts:
        if "?" in value:
            raise UrlError(
                f"holds a URL whose {what} has a ?, which the MySQL scanner reads as the start of "
                "its connection attributes"
            )
    if read.host == "localhost" and not any(name == "socket" for name, _ in read.parameters):
        raise UrlError(
            "holds a MySQL URL whose host is localhost, which its client reads as a default local "
            "socket: write the loopback address, or give socket"
        )


__all__ = [
    "MYSQL_NAME_CHARACTERS",
    "PARAMETERS",
    "POSTGRES_NAME_BYTES",
    "POSTGRES_OPTIONS",
    "SCHEMES",
    "ServerKind",
    "ServerUrl",
    "UrlError",
    "mysql_quotable",
    "parse",
]
