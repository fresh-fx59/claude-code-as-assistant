from __future__ import annotations

from dataclasses import dataclass

from .state_store import StateStore


@dataclass
class AppContext:
    """Runtime dependency container for incremental DI migration."""

    provider_manager: object
    session_manager: object
    memory_manager: object
    task_manager: object | None
    schedule_manager: object | None
    state_store: StateStore

