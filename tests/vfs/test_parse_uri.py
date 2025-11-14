from nova_navigator.vfs2.parse_uri import ParseResult, URIComponent, parse_uri


def test_no_schema() -> None:
    result = parse_uri("/path/to/local/file.txt")
    expected = ParseResult(components=[URIComponent(scheme=None, netloc=None, path="/path/to/local/file.txt")])
    assert result == expected

    result = parse_uri("rel_path/to/local/file.txt")
    expected = ParseResult(components=[URIComponent(scheme=None, netloc=None, path="rel_path/to/local/file.txt")])
    assert result == expected


def test_special_cases() -> None:
    result = parse_uri("/")
    expected = ParseResult(components=[URIComponent(scheme=None, netloc=None, path="/")])
    assert result == expected

    result = parse_uri("")
    expected = ParseResult(components=[URIComponent(scheme=None, netloc=None, path="")])
    assert result == expected


def test_single_schema() -> None:
    result = parse_uri("file:///path/to/local/file.txt")
    expected = ParseResult(components=[URIComponent(scheme="file", netloc=None, path="/path/to/local/file.txt")])
    assert result == expected

    result = parse_uri("ssh://user@host:22/path/to/remote/file.txt")
    expected = ParseResult(
        components=[URIComponent(scheme="ssh", netloc="user@host:22", path="/path/to/remote/file.txt")]
    )
    assert result == expected


def test_relative_path_with_schema() -> None:
    result = parse_uri("file://rel/path/to/local/file.txt")
    # rel path is treated as netloc here
    expected = ParseResult(components=[URIComponent(scheme="file", netloc="rel", path="/path/to/local/file.txt")])
    assert result == expected


def test_nested_schema() -> None:
    result = parse_uri("ssh://user@host:22/path/to/archive.tar.gz/tar://path/file.txt")
    expected = ParseResult(
        components=[
            URIComponent(scheme="ssh", netloc="user@host:22", path="/path/to/archive.tar.gz"),
            URIComponent(scheme="tar", netloc=None, path="/path/file.txt"),
        ]
    )
    assert result == expected

    result = parse_uri("path/to/archive.tar.gz/tar://path/file.txt")
    expected = ParseResult(
        components=[
            URIComponent(scheme=None, netloc=None, path="path/to/archive.tar.gz"),
            URIComponent(scheme="tar", netloc=None, path="/path/file.txt"),
        ]
    )
    assert result == expected

    result = parse_uri("ssh://user@host:22/path/to/archive.tar.gz/tar://path/file.zip/zip://file.txt")
    expected = ParseResult(
        components=[
            URIComponent(scheme="ssh", netloc="user@host:22", path="/path/to/archive.tar.gz"),
            URIComponent(scheme="tar", netloc=None, path="/path/file.zip"),
            URIComponent(scheme="zip", netloc=None, path="/file.txt"),
        ]
    )
    assert result == expected


def test_invalid_uri() -> None:
    result = parse_uri("://missing/scheme/and/netloc")
    expected = ParseResult(components=[URIComponent(scheme=None, netloc=None, path="://missing/scheme/and/netloc")])
    assert result == expected

    result = parse_uri("ssh:///missing/netloc/only/path")
    expected = ParseResult(components=[URIComponent(scheme="ssh", netloc=None, path="/missing/netloc/only/path")])
    assert result == expected

    result = parse_uri("ssh://")
    expected = ParseResult(components=[URIComponent(scheme="ssh", netloc=None, path="")])
    assert result == expected
