"""Shell-like tokenization with glob-expansion metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Token:
    """A single token from a shell input line.

    Attributes:
        value: The token text (with quotes/escapes resolved).
        quoted: True if the token was quoted (single, double, or backslash).
                Quoted tokens are not subject to glob expansion.
    """

    value: str
    quoted: bool


def tokenize(line: str) -> list[Token]:
    """Split a line into tokens using shell quoting rules.

    Uses a hand-written lexer (not shlex) to track whether each token
    was quoted, which suppresses glob expansion.

    Handles:
    - Single quotes: literal content, no escapes
    - Double quotes: resolves backslash escapes inside
    - Backslash outside quotes: escapes next character
    - Unquoted tokens: subject to glob expansion

    Returns an empty list for empty/whitespace-only input.
    """
    if not line.strip():
        return []
    return _split_with_quoting_info(line)


def _split_with_quoting_info(line: str) -> list[Token]:
    """Parse line tracking whether each token was quoted."""
    tokens: list[Token] = []
    i = 0
    n = len(line)

    while i < n:
        # Skip whitespace
        while i < n and line[i] in " \t":
            i += 1
        if i >= n:
            break

        # Collect one token
        value_parts: list[str] = []
        quoted = False

        while i < n and line[i] not in " \t":
            ch = line[i]
            if ch == "'":
                quoted = True
                i += 1
                while i < n and line[i] != "'":
                    value_parts.append(line[i])
                    i += 1
                if i < n:
                    i += 1  # skip closing quote
            elif ch == '"':
                quoted = True
                i += 1
                while i < n and line[i] != '"':
                    if line[i] == "\\" and i + 1 < n:
                        i += 1
                        value_parts.append(line[i])
                    else:
                        value_parts.append(line[i])
                    i += 1
                if i < n:
                    i += 1  # skip closing quote
            elif ch == "\\":
                quoted = True
                i += 1
                if i < n:
                    value_parts.append(line[i])
                    i += 1
            else:
                value_parts.append(ch)
                i += 1

        if value_parts or quoted:
            tokens.append(Token("".join(value_parts), quoted))

    return tokens
