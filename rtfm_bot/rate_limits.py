from __future__ import annotations

import math
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Simple in-memory sliding window limiter."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str | int, deque[float]] = defaultdict(deque)

    def retry_after(self, key: str | int) -> int:
        now = time.monotonic()
        bucket = self._events[key]
        self._trim(bucket, now)

        if len(bucket) < self.limit:
            return 0

        oldest = bucket[0]
        remaining = self.window_seconds - (now - oldest)
        return max(1, math.ceil(remaining))

    def hit(self, key: str | int) -> None:
        now = time.monotonic()
        bucket = self._events[key]
        self._trim(bucket, now)
        bucket.append(now)

    def _trim(self, bucket: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

