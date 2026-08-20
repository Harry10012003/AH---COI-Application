from __future__ import annotations

from collections import defaultdict, deque
import math
import threading
import time


class QueryMetricRegistry:
    """Small in-memory rolling window; never stores SQL text or parameters."""

    def __init__(self, window_size: int = 500) -> None:
        self._window_size = max(20, int(window_size))
        self._durations: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._window_size)
        )
        self._outcomes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._lock = threading.Lock()

    def record(self, source: str, duration_sec: float, outcome: str) -> None:
        key = str(source or "unknown").strip().lower() or "unknown"
        result = str(outcome or "ok").strip().lower() or "ok"
        with self._lock:
            self._durations[key].append(max(0.0, float(duration_sec)))
            self._outcomes[key][result] += 1

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
        return ordered[index]

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            sources = set(self._durations) | set(self._outcomes)
            return {
                source: {
                    "sample_count": len(self._durations[source]),
                    "p50_ms": round(self._percentile(list(self._durations[source]), 0.50) * 1000, 1),
                    "p95_ms": round(self._percentile(list(self._durations[source]), 0.95) * 1000, 1),
                    "outcomes": dict(self._outcomes[source]),
                }
                for source in sorted(sources)
            }


query_metrics = QueryMetricRegistry()


def timed_execute(source: str, execute):
    started = time.perf_counter()
    try:
        result = execute()
    except Exception as exc:
        name = type(exc).__name__.lower()
        outcome = "timeout" if "timeout" in str(exc).lower() else name
        query_metrics.record(source, time.perf_counter() - started, outcome)
        raise
    query_metrics.record(source, time.perf_counter() - started, "ok")
    return result

