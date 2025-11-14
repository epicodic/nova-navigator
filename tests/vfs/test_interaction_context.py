import threading
from unittest.mock import Mock

from nova_navigator.vfs2.interaction_context import DecisionChoice, InteractionContext, Message


def test_decision() -> None:
    progress_callback = Mock()
    decision_callback = Mock()
    cancel_event = threading.Event()
    context = InteractionContext(
        cancel_event=cancel_event,
        progress_callback=progress_callback,
        decision_callback=decision_callback,
    )

    context.request_decision(Message("Test Message"), data=42)
    decision_callback.assert_called_once()
    decision_id = decision_callback.call_args[0][1]
    message = decision_callback.call_args[0][2]
    assert message.message == "Test Message"

    assert len(context._pending_decisions) == 1
    assert len(context._answered_decisions) == 0

    context.request_decision(Message("Test Message"), data=43)
    context.request_decision(Message("Other Message"), data=44)
    other_decision_id = decision_callback.call_args[0][1]
    assert decision_callback.call_count == 3
    assert len(context._pending_decisions) == 3
    assert len(context._answered_decisions) == 0

    context.send_decision_response(decision_id, choice=DecisionChoice.YES_TO_ALL)
    assert len(context._pending_decisions) == 1
    assert len(context._answered_decisions) == 2

    context.request_decision(Message("Test Message"), data=45)
    assert decision_callback.call_count == 3  # no new call
    assert len(context._pending_decisions) == 1
    assert len(context._answered_decisions) == 3
    assert context._pending_decisions[other_decision_id].data == 44

    context.send_decision_response(other_decision_id, choice=DecisionChoice.NO)
    assert len(context._pending_decisions) == 0
    assert len(context._answered_decisions) == 4
