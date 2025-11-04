from wcwidth import wcswidth


def ljust(s: str, width: int, fillchar: str = " ") -> str:
    return s + fillchar * max(0, width - wcswidth(s))


def rjust(s: str, width: int, fillchar: str = " ") -> str:
    return fillchar * max(0, width - wcswidth(s)) + s


def center(s: str, width: int, fillchar: str = " ") -> str:
    total_padding = max(0, width - wcswidth(s))
    left_padding = total_padding // 2
    right_padding = total_padding - left_padding
    return fillchar * left_padding + s + fillchar * right_padding
