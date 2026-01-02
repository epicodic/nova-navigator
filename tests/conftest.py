import threading
from unittest.mock import Mock

import pytest

from nova_navigator.vfs2.interaction_context import InteractionContext


@pytest.fixture
def interaction_context_mock() -> InteractionContext:
    progress_callback = Mock()
    decision_callback = Mock()
    cancel_event = threading.Event()
    return InteractionContext(
        cancel_event=cancel_event,
        progress_callback=progress_callback,
        decision_callback=decision_callback,
    )
