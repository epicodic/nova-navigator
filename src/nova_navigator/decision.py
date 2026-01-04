from enum import Flag
from typing import Any


class Decision(Flag):
    # base decisions
    _POSITIVE = 0x0000
    _NEGATIVE = 0x0001

    # modifiers
    _MODIFIER_TO_ALL = 0x10000

    # the actual decisions
    YES = 0x0002 | _POSITIVE
    NO = 0x0002 | _NEGATIVE
    OK = 0x0004 | _POSITIVE
    CANCEL = 0x0004 | _NEGATIVE
    RETRY = 0x0008 | _POSITIVE
    SKIP = 0x0008 | _NEGATIVE

    # mask to extract the base decision (YES, NO, OK, CANCEL, RETRY, SKIP)
    _MASK = 0xFFFF

    # composite decisions
    ALL = YES | _MODIFIER_TO_ALL
    NONE = NO | _MODIFIER_TO_ALL
    SKIP_ALL = SKIP | _MODIFIER_TO_ALL

    @staticmethod
    def _is_set(value: "Decision", flag: Any) -> bool:
        return bool(value & Decision(flag))

    @property
    def is_to_all(self) -> bool:
        return self._is_set(self, self._MODIFIER_TO_ALL)

    @property
    def is_positive(self) -> bool:
        return not self.is_negative

    @property
    def is_negative(self) -> bool:
        return self._is_set(self, self._NEGATIVE)

    def is_decision(self, value: "Decision") -> bool:
        return bool(self & Decision(self._MASK)) == value

    def __str__(self) -> str:
        name = self.name.replace("_", " ")
        # capitalize first letter of each word
        name = " ".join(word.capitalize() for word in name.split())
        return name

    @property
    def tr(self) -> str:
        return str(self)
