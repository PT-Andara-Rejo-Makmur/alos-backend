"""Lightweight in-memory rate limiting abstraction for sensitive endpoints."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Protocol

from alos.security.errors import PlatformError


class RateLimiter(Protocol):
    async def check(self, key: str, max_requests: int, window_seconds: float) -> None: ...


class InMemoryRateLimiter:
    """Thread-safe and async-safe in-memory sliding window rate limiter."""

    def __init__(self) -> None:
        self._records: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str, max_requests: int, window_seconds: float) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        async with self._lock:
            timestamps = self._records[key]
            while timestamps and timestamps[0] < cutoff:
                timestamps.popleft()
            if len(timestamps) >= max_requests:
                raise PlatformError(
                    "RATE_LIMIT_EXCEEDED",
                    "too many requests, please try again later",
                    status_code=429,
                )
            timestamps.append(now)

    async def reset(self) -> None:
        async with self._lock:
            self._records.clear()
