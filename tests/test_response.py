import pytest

from nova_navigator.response import Response, ResponseRole

# --- is_accepted ---


def test_ok_is_accepted() -> None:
    assert Response.OK.is_accepted


def test_save_is_accepted() -> None:
    assert Response.SAVE.is_accepted


def test_yes_is_accepted() -> None:
    assert Response.YES.is_accepted


def test_retry_is_accepted() -> None:
    assert Response.RETRY.is_accepted


def test_overwrite_is_accepted() -> None:
    assert Response.OVERWRITE.is_accepted


def test_overwrite_all_is_accepted() -> None:
    assert Response.OVERWRITE_ALL.is_accepted


def test_cancel_is_not_accepted() -> None:
    assert not Response.CANCEL.is_accepted


def test_no_is_not_accepted() -> None:
    assert not Response.NO.is_accepted


def test_discard_is_not_accepted() -> None:
    assert not Response.DISCARD.is_accepted


# --- is_rejected ---


def test_cancel_is_rejected() -> None:
    assert Response.CANCEL.is_rejected


def test_no_is_rejected() -> None:
    assert Response.NO.is_rejected


def test_skip_is_rejected() -> None:
    assert Response.SKIP.is_rejected


def test_abort_is_rejected() -> None:
    assert Response.ABORT.is_rejected


def test_ok_is_not_rejected() -> None:
    assert not Response.OK.is_rejected


def test_yes_is_not_rejected() -> None:
    assert not Response.YES.is_rejected


def test_discard_is_not_rejected() -> None:
    assert not Response.DISCARD.is_rejected


# --- is_to_all ---


def test_save_all_is_to_all() -> None:
    assert Response.SAVE_ALL.is_to_all


def test_yes_to_all_is_to_all() -> None:
    assert Response.ALL.is_to_all


def test_no_to_all_is_to_all() -> None:
    assert Response.NONE.is_to_all


def test_skip_all_is_to_all() -> None:
    assert Response.SKIP_ALL.is_to_all


def test_overwrite_all_is_to_all() -> None:
    assert Response.OVERWRITE_ALL.is_to_all


def test_discard_all_is_to_all() -> None:
    assert Response.DISCARD_ALL.is_to_all


def test_ignore_all_is_to_all() -> None:
    assert Response.IGNORE_ALL.is_to_all


def test_ok_is_not_to_all() -> None:
    assert not Response.OK.is_to_all


def test_cancel_is_not_to_all() -> None:
    assert not Response.CANCEL.is_to_all


def test_skip_is_not_to_all() -> None:
    assert not Response.SKIP.is_to_all


# --- to_all variants keep their role ---


def test_yes_to_all_is_accepted() -> None:
    assert Response.ALL.is_accepted


def test_no_to_all_is_rejected() -> None:
    assert Response.NONE.is_rejected


def test_skip_all_is_rejected() -> None:
    assert Response.SKIP_ALL.is_rejected


# --- role property ---


def test_ok_role_is_accept() -> None:
    assert Response.OK.role == ResponseRole.ACCEPT


def test_cancel_role_is_reject() -> None:
    assert Response.CANCEL.role == ResponseRole.REJECT


def test_discard_role_is_destructive() -> None:
    assert Response.DISCARD.role == ResponseRole.DESTRUCTIVE


def test_apply_role_is_apply() -> None:
    assert Response.APPLY.role == ResponseRole.APPLY


def test_reset_role_is_reset() -> None:
    assert Response.RESET.role == ResponseRole.RESET


def test_help_role_is_help() -> None:
    assert Response.HELP.role == ResponseRole.HELP


def test_to_all_excluded_from_role() -> None:
    # TO_ALL is a modifier, not a role — role should return only the role bit
    assert Response.ALL.role == ResponseRole.ACCEPT
    assert Response.SKIP_ALL.role == ResponseRole.REJECT


# --- equality ---


def test_identity_equality() -> None:
    assert Response.OK == Response.OK
    assert Response.CANCEL == Response.CANCEL


def test_distinct_values_not_equal() -> None:
    assert Response.OK != Response.CANCEL
    assert Response.YES != Response.NO
    assert Response.SKIP != Response.SKIP_ALL


# --- __str__ / tr ---


def test_str_ok() -> None:
    assert str(Response.OK) == "Ok"


def test_str_cancel() -> None:
    assert str(Response.CANCEL) == "Cancel"


def test_str_yes_to_all() -> None:
    assert str(Response.ALL) == "All"


def test_str_restore_defaults() -> None:
    assert str(Response.RESTORE_DEFAULTS) == "Restore Defaults"


def test_tr_equals_str() -> None:
    for r in (Response.OK, Response.CANCEL, Response.YES, Response.SKIP_ALL):
        assert r.tr == str(r)


# --- custom factory ---


def test_custom_accept() -> None:
    r = Response.custom(128, ResponseRole.ACCEPT)
    assert r.is_accepted
    assert not r.is_rejected
    assert not r.is_to_all


def test_custom_reject() -> None:
    r = Response.custom(200, ResponseRole.REJECT)
    assert r.is_rejected
    assert not r.is_accepted


def test_custom_with_to_all() -> None:
    r = Response.custom(128, ResponseRole.ACCEPT | ResponseRole.TO_ALL)
    assert r.is_accepted
    assert r.is_to_all


def test_custom_role_property() -> None:
    r = Response.custom(128, ResponseRole.ACTION)
    assert r.role == ResponseRole.ACTION


def test_custom_value_id_too_low_raises() -> None:
    with pytest.raises(ValueError, match="value_id must be in range"):
        Response.custom(1, ResponseRole.ACCEPT)


def test_custom_value_id_boundary() -> None:
    Response.custom(128, ResponseRole.ACCEPT)  # lowest valid
    Response.custom(0xFFFF, ResponseRole.ACCEPT)  # highest valid


def test_custom_value_id_too_high_raises() -> None:
    with pytest.raises(ValueError, match="value_id must be in range"):
        Response.custom(0x10000, ResponseRole.ACCEPT)
