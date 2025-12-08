from wcwidth import wcswidth


def ljust(s: str, width: int, fillchar: str = " ") -> str:
    """Left-justify *s* in a field of *width* terminal columns, padding with *fillchar*.

    Unlike :meth:`str.ljust`, this uses :func:`wcwidth.wcswidth` to measure
    the display width, so wide Unicode characters (CJK, emoji, etc.) are
    counted correctly.
    """
    return s + fillchar * max(0, width - wcswidth(s))


def rjust(s: str, width: int, fillchar: str = " ") -> str:
    """Right-justify *s* in a field of *width* terminal columns, padding with *fillchar*.

    Uses display width (via :func:`wcwidth.wcswidth`) rather than codepoint
    count.
    """
    return fillchar * max(0, width - wcswidth(s)) + s


def center(s: str, width: int, fillchar: str = " ") -> str:
    """Centre *s* in a field of *width* terminal columns, padding with *fillchar*.

    Uses display width (via :func:`wcwidth.wcswidth`) rather than codepoint
    count.
    """
    total_padding = max(0, width - wcswidth(s))
    left_padding = total_padding // 2
    right_padding = total_padding - left_padding
    return fillchar * left_padding + s + fillchar * right_padding
