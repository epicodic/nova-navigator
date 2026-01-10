from nova_navigator.decision import Decision

# --- is_decision ---
# Strips the _MODIFIER_TO_ALL bit and compares the base decision to the given
# value, so e.g. ALL.is_decision(YES) is True.


def test_yes_is_yes() -> None:
    assert Decision.YES.is_decision(Decision.YES)


def test_no_is_no() -> None:
    assert Decision.NO.is_decision(Decision.NO)


def test_ok_is_ok() -> None:
    assert Decision.OK.is_decision(Decision.OK)


def test_cancel_is_cancel() -> None:
    assert Decision.CANCEL.is_decision(Decision.CANCEL)


def test_retry_is_retry() -> None:
    assert Decision.RETRY.is_decision(Decision.RETRY)


def test_skip_is_skip() -> None:
    assert Decision.SKIP.is_decision(Decision.SKIP)


def test_yes_is_not_no() -> None:
    assert not Decision.YES.is_decision(Decision.NO)


def test_ok_is_not_cancel() -> None:
    assert not Decision.OK.is_decision(Decision.CANCEL)


def test_all_is_yes() -> None:
    # ALL is YES with the to-all modifier; stripping the modifier should give YES
    assert Decision.ALL.is_decision(Decision.YES)


def test_none_is_no() -> None:
    # NONE is NO with the to-all modifier; stripping the modifier should give NO
    assert Decision.NONE.is_decision(Decision.NO)


def test_skip_all_is_skip() -> None:
    # SKIP_ALL is SKIP with the to-all modifier
    assert Decision.SKIP_ALL.is_decision(Decision.SKIP)


def test_all_is_not_no() -> None:
    assert not Decision.ALL.is_decision(Decision.NO)


def test_none_is_not_yes() -> None:
    assert not Decision.NONE.is_decision(Decision.YES)


# --- is_to_all ---


def test_all_is_to_all() -> None:
    assert Decision.ALL.is_to_all


def test_none_is_to_all() -> None:
    assert Decision.NONE.is_to_all


def test_skip_all_is_to_all() -> None:
    assert Decision.SKIP_ALL.is_to_all


def test_yes_is_not_to_all() -> None:
    assert not Decision.YES.is_to_all


def test_no_is_not_to_all() -> None:
    assert not Decision.NO.is_to_all


def test_skip_is_not_to_all() -> None:
    assert not Decision.SKIP.is_to_all


# --- is_negative / is_positive ---


def test_no_is_negative() -> None:
    assert Decision.NO.is_negative


def test_cancel_is_negative() -> None:
    assert Decision.CANCEL.is_negative


def test_skip_is_negative() -> None:
    assert Decision.SKIP.is_negative


def test_none_is_negative() -> None:
    assert Decision.NONE.is_negative


def test_skip_all_is_negative() -> None:
    assert Decision.SKIP_ALL.is_negative


def test_yes_is_positive() -> None:
    assert Decision.YES.is_positive


def test_ok_is_positive() -> None:
    assert Decision.OK.is_positive


def test_retry_is_positive() -> None:
    assert Decision.RETRY.is_positive


def test_all_is_positive() -> None:
    assert Decision.ALL.is_positive


# --- __str__ / tr ---


def test_str_yes() -> None:
    assert str(Decision.YES) == "Yes"


def test_str_skip_all() -> None:
    assert str(Decision.SKIP_ALL) == "Skip All"


def test_tr_equals_str() -> None:
    for decision in (Decision.YES, Decision.NO, Decision.ALL, Decision.SKIP_ALL):
        assert decision.tr == str(decision)
