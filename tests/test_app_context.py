from src.bot import get_app_context, set_app_context, _state_store
from src.features.app_context import AppContext
from src.features.state_store import get_default_state_store


class _DummyStateStore:
    pass


def test_state_store_uses_injected_app_context() -> None:
    dummy_store = _DummyStateStore()
    set_app_context(
        AppContext(
            provider_manager=object(),
            session_manager=object(),
            memory_manager=object(),
            task_manager=None,
            schedule_manager=None,
            state_store=dummy_store,  # type: ignore[arg-type]
        )
    )
    assert _state_store() is dummy_store

    set_app_context(None)
    assert _state_store() is get_default_state_store()
    assert get_app_context() is None

