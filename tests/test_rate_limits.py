from __future__ import annotations

import unittest
from unittest.mock import patch

from rtfm_bot.rate_limits import SlidingWindowRateLimiter


class SlidingWindowRateLimiterTests(unittest.TestCase):
    def test_status_reports_usage_and_cooldown(self) -> None:
        limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60)

        with patch("rtfm_bot.rate_limits.time.monotonic", return_value=100.0):
            limiter.hit("global")
            limiter.hit("global")

        with patch("rtfm_bot.rate_limits.time.monotonic", return_value=100.0):
            status = limiter.status("global")

        self.assertEqual(status.used, 2)
        self.assertEqual(status.remaining, 0)
        self.assertEqual(status.retry_after, 60)
        self.assertEqual(status.resets_in, 60)

    def test_status_trims_old_events(self) -> None:
        limiter = SlidingWindowRateLimiter(limit=1, window_seconds=30)

        with patch("rtfm_bot.rate_limits.time.monotonic", return_value=10.0):
            limiter.hit("channel")

        with patch("rtfm_bot.rate_limits.time.monotonic", return_value=41.0):
            status = limiter.status("channel")

        self.assertEqual(status.used, 0)
        self.assertEqual(status.remaining, 1)
        self.assertEqual(status.retry_after, 0)
        self.assertEqual(status.resets_in, 0)

    def test_status_can_use_dynamic_limit(self) -> None:
        limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60)

        with patch("rtfm_bot.rate_limits.time.monotonic", return_value=100.0):
            limiter.hit("global")
            limiter.hit("global")

        with patch("rtfm_bot.rate_limits.time.monotonic", return_value=100.0):
            status = limiter.status("global", limit=3)

        self.assertEqual(status.used, 2)
        self.assertEqual(status.limit, 3)
        self.assertEqual(status.remaining, 1)
        self.assertEqual(status.retry_after, 0)


if __name__ == "__main__":
    unittest.main()
