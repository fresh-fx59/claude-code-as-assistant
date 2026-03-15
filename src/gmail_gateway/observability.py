from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class GatewayObservability:
    counters: Counter[str] = field(default_factory=Counter)

    def inc(self, key: str) -> None:
        self.counters[key] += 1

    def snapshot(self) -> dict[str, int]:
        return dict(self.counters)
