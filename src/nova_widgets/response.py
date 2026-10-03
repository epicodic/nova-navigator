from enum import IntFlag, nonmember


class ResponseRole(IntFlag):
    """Role flags carried by a `Response` value.

    Each predefined `Response` carries exactly one role bit (plus the optional
    `TO_ALL` modifier).
    Role bits and the TO_ALL modifier occupy the upper 16 bits; the lower 16
    bits hold a sequential identity that is unique within a role group.

    Bit layout::

        bit  23    : TO_ALL modifier
        bits 22-16 : role flags (one per predefined Response)
        bits 15-0  : sequential value ID (1-127 reserved; >= 128 user-defined)
    """

    ACCEPT = 1 << 16  # confirms/applies the operation (OK, Save, Retry...)
    REJECT = 1 << 17  # cancels/aborts without side-effects (Cancel, Close...)
    DESTRUCTIVE = 1 << 18  # irreversible destructive action (Discard...)
    ACTION = 1 << 19  # generic action button (user-defined)
    HELP = 1 << 20  # opens help
    APPLY = 1 << 21  # applies changes without closing the dialog
    RESET = 1 << 22  # resets fields to a known state

    # Modifier — OR'd with any role to produce a "to all" variant.
    TO_ALL = 1 << 23

    # Mask covering the entire upper 16 bits (all role flags + TO_ALL modifier).
    ROLE_MASK = nonmember(0xFFFF_0000)


class Response(IntFlag):
    """Combined button identity and role for dialog responses.

    Each predefined value encodes a sequential ID in the lower 16 bits and a
    `ResponseRole` flag in the upper 16 bits.  Use the properties to inspect
    role and modifier::

        response.is_accepted   # True for AcceptRole responses
        response.is_rejected   # True for RejectRole responses
        response.is_to_all     # True when the TO_ALL modifier is set
        response.role          # the ResponseRole bits only

    Equality comparison works directly::

        if result == Response.OK: ...
    """

    # --- AcceptRole ---
    OK = 1 | ResponseRole.ACCEPT
    OPEN = 2 | ResponseRole.ACCEPT
    SAVE = 3 | ResponseRole.ACCEPT
    SAVE_ALL = 4 | ResponseRole.ACCEPT | ResponseRole.TO_ALL
    RETRY = 5 | ResponseRole.ACCEPT
    IGNORE = 6 | ResponseRole.ACCEPT
    IGNORE_ALL = 7 | ResponseRole.ACCEPT | ResponseRole.TO_ALL
    YES = 8 | ResponseRole.ACCEPT
    ALL = 9 | ResponseRole.ACCEPT | ResponseRole.TO_ALL  # yes to all
    OVERWRITE = 10 | ResponseRole.ACCEPT
    OVERWRITE_ALL = 11 | ResponseRole.ACCEPT | ResponseRole.TO_ALL

    # --- RejectRole ---
    CANCEL = 12 | ResponseRole.REJECT
    CLOSE = 13 | ResponseRole.REJECT
    ABORT = 14 | ResponseRole.REJECT
    NO = 15 | ResponseRole.REJECT
    NONE = 16 | ResponseRole.REJECT | ResponseRole.TO_ALL
    SKIP = 17 | ResponseRole.REJECT
    SKIP_ALL = 18 | ResponseRole.REJECT | ResponseRole.TO_ALL

    # --- DestructiveRole ---
    DISCARD = 19 | ResponseRole.DESTRUCTIVE
    DISCARD_ALL = 20 | ResponseRole.DESTRUCTIVE | ResponseRole.TO_ALL

    # --- ApplyRole ---
    APPLY = 21 | ResponseRole.APPLY

    # --- ResetRole ---
    RESET = 22 | ResponseRole.RESET
    RESTORE_DEFAULTS = 23 | ResponseRole.RESET

    # --- HelpRole ---
    HELP = 24 | ResponseRole.HELP

    # Reserved value-ID range for user-defined responses.
    USER_VALUE_MIN = nonmember(128)
    USER_VALUE_MAX = nonmember(0xFFFF)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def custom(cls, value_id: int, role: ResponseRole) -> "Response":
        """Create a user-defined Response with the given ID and role.

        Args:
            value_id: Sequential identity in the range 128-65535 (bits 15-7).
                Values 1-127 are reserved for predefined responses.
            role: The ResponseRole for this button.

        Returns:
            A Response instance encoding value_id | role.
        """
        if not (cls.USER_VALUE_MIN <= value_id <= cls.USER_VALUE_MAX):
            raise ValueError(f"value_id must be in range {cls.USER_VALUE_MIN}-{cls.USER_VALUE_MAX}; got {value_id}")
        return cls(value_id | int(role))

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def role(self) -> ResponseRole:
        """The ResponseRole bits of this response (excludes TO_ALL and value ID)."""
        return ResponseRole(int(self) & ResponseRole.ROLE_MASK & ~int(ResponseRole.TO_ALL))

    @property
    def is_accepted(self) -> bool:
        """True when this response carries the AcceptRole."""
        return bool(int(self) & ResponseRole.ACCEPT)

    @property
    def is_rejected(self) -> bool:
        """True when this response carries the RejectRole."""
        return bool(int(self) & ResponseRole.REJECT)

    @property
    def is_to_all(self) -> bool:
        """True when the TO_ALL modifier is set."""
        return bool(int(self) & ResponseRole.TO_ALL)

    def __str__(self) -> str:
        name = self.name or repr(self)
        return " ".join(word.capitalize() for word in name.replace("_", " ").split())

    @property
    def tr(self) -> str:
        return str(self)
