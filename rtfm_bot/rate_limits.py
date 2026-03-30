from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(slots=True)
class RateLimitStatus:
    key: str | int
    used: int
    limit: int
    window_seconds: int
    remaining: int
    retry_after: int
    resets_in: int


class SlidingWindowRateLimiter:
    """Simple in-memory sliding window limiter."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str | int, deque[float]] = defaultdict(deque)

    def retry_after(self, key: str | int, *, limit: int | None = None) -> int:
        return self.status(key, limit=limit).retry_after

    def status(self, key: str | int, *, limit: int | None = None) -> RateLimitStatus:
        now = time.monotonic()
        bucket = self._events[key]
        self._trim(bucket, now)
        effective_limit = max(1, limit or self.limit)

        used = len(bucket)
        retry_after = 0
        resets_in = 0

        if bucket:
            oldest = bucket[0]
            resets_in = max(1, math.ceil(self.window_seconds - (now - oldest)))
            if used >= effective_limit:
                retry_after = resets_in

        return RateLimitStatus(
            key=key,
            used=used,
            limit=effective_limit,
            window_seconds=self.window_seconds,
            remaining=max(0, effective_limit - used),
            retry_after=retry_after,
            resets_in=resets_in,
        )

    def hit(self, key: str | int) -> None:
        now = time.monotonic()
        bucket = self._events[key]
        self._trim(bucket, now)
        bucket.append(now)

    def _trim(self, bucket: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
