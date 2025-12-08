import re
from dataclasses import dataclass

SCHEME_SEPARATOR = "://"
PATH_SEPARATOR = "/"

# General URI format:
#   [<scheme://>][<netloc>]<path_with_nested_schemes>
#
# where <path_with_nested_schemes> can contain further schemes, e.g.:
#   <path_to_archive>/<nested_scheme>://<path_inside_archive>
# netloc is not allowed for nested schemes.
#
# Examples:
#   /path/to/local/file.txt                         -> [(scheme=None, netloc=None, path=/path/to/local/file.txt)]
#   rel_path/to/local/file.txt                      -> [(scheme=None, netloc=None, path=rel_path/to/local/file.txt)]
#   file:///path/to/local/file.txt                  -> [(scheme="file", netloc=None, path=/path/to/local/file.txt)]
#   ssh://user@host:22/path/to/remote/file.txt
#   -> [(scheme="ssh", netloc="user@host:22", path=/path/to/remote/file.txt)]
#   ssh://user@host:22/path/to/archive.tar.gz/tar://file.txt
#   -> [(scheme="ssh",netloc="user@host:22", path=/path/to/archive.tar.gz),(scheme="tar", netloc=None, path=/file.txt)]


@dataclass
class URIComponent:
    """A single component of a parsed URI.

    Represents one layer of a (possibly nested) URI, holding the optional
    *scheme* (e.g. ``"ssh"``), optional *netloc* (e.g. ``"user@host:22"``),
    and mandatory *path* segment.
    """

    scheme: str | None
    netloc: str | None
    path: str


@dataclass
class ParseResult:
    """The result of parsing a (possibly nested) URI into its components.

    ``components[0]`` is the outermost layer; subsequent elements represent
    nested filesystem schemes found inside the path, e.g. a tar archive
    embedded in an SSH path.
    """

    components: list[URIComponent]


# matches [scheme://netloc]/remainder
_OUTER_PATTERN_REGEX = re.compile(r"^(?:([^:/]+)://([^/]*))?(.*)$")

# matches [/scheme://]remainder
_NESTED_SCHEMA_REGEX = re.compile(r"/(\w+)://")


def parse_uri(uri: str) -> ParseResult:
    """Parse a URI that may contain nested filesystem schemes.

    A nested URI embeds additional scheme references inside the path, separated
    by ``/<scheme>://``.  For example::

        ssh://host/archive.tar.gz/tar://file.txt

    is split into two :class:`URIComponent` entries: one for the SSH layer
    (path ``/archive.tar.gz``) and one for the tar layer (path ``/file.txt``).

    Raises :exc:`ValueError` for syntactically invalid URIs.
    """
    components: list[URIComponent] = []

    uri = uri.strip()

    match = _OUTER_PATTERN_REGEX.match(uri)
    if not match:
        raise ValueError(f"Invalid URI: {uri}")

    scheme, netloc, remainder = match.groups()

    if netloc is not None and len(netloc) == 0:
        netloc = None

    while True:
        nested_match = _NESTED_SCHEMA_REGEX.search(remainder)
        if not nested_match:
            # no more nested schemes
            components.append(URIComponent(scheme, netloc, remainder))
            break

        nested_scheme = nested_match.group(1)
        index = nested_match.start()

        path_before_nested = remainder[:index]
        components.append(URIComponent(scheme, netloc, path_before_nested))

        # update for next iteration
        scheme = nested_scheme
        netloc = None
        remainder = "/" + remainder[index + 1 + len(nested_scheme) + len(SCHEME_SEPARATOR) :]

    return ParseResult(components)
