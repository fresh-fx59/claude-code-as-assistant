from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .state_store import StateStore

if TYPE_CHECKING:
    from ..memory import MemoryManager
    from ..providers import ProviderManager
    from ..scheduler import ScheduleManager
    from ..sessions import SessionManager
    from ..tasks import TaskManager


@dataclass
class AppContext:
    """Runtime dependency container for incremental DI migration."""

    provider_manager: ProviderManager
    session_manager: SessionManager
    memory_manager: MemoryManager
    task_manager: TaskManager | None
    schedule_manager: ScheduleManager | None
    state_store: StateStore
