from __future__ import annotations

from collections import deque
import threading


class InteractiveGoQueue:
    """Bounded, thread-safe, deduplicated queue for user-requested GO work."""

    def __init__(self, capacity: int) -> None:
        self._capacity = max(1, int(capacity))
        self._items: deque[str] = deque()
        self._keys: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _normalize(value: object) -> str:
        return str(value or "").strip().upper()

    def promote(self, values: list[str]) -> int:
        clean: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            key = self._normalize(value)
            if key and key not in seen:
                seen.add(key)
                clean.append(key)
        if not clean:
            return self.size()
        with self._lock:
            promoted = set(clean)
            if promoted & self._keys:
                self._items = deque(item for item in self._items if item not in promoted)
            for key in reversed(clean):
                self._items.appendleft(key)
            while len(self._items) > self._capacity:
                self._items.pop()
            self._keys = set(self._items)
            return len(self._items)

    def take(self, limit: int) -> list[str]:
        selected: list[str] = []
        with self._lock:
            for _ in range(max(0, int(limit or 0))):
                if not self._items:
                    break
                key = self._items.popleft()
                self._keys.discard(key)
                selected.append(key)
        return selected

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def snapshot(self, limit: int = 20) -> list[str]:
        with self._lock:
            return list(self._items)[: max(0, int(limit or 0))]

