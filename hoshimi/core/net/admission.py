"""Shared provider concurrency and cooldowns, held until a stream closes."""

from __future__ import annotations

import math
import threading
import time
from typing import Any


class AdmissionDelayed(Exception):
    def __init__(self, seconds: float):
        self.seconds = seconds


def admission_options(raw: Any) -> tuple[int, float, float]:
    if raw is None:
        return 2, 0.0, 8.0
    if not isinstance(raw, dict):
        raise TypeError("admission must be an object")
    concurrency = raw.get("max_concurrency", 2)
    interval = raw.get("min_interval_seconds", 0.0)
    timeout = raw.get("queue_timeout_seconds", 8.0)
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
        raise ValueError("admission.max_concurrency must be a positive integer")
    for name, value in (("min_interval_seconds", interval), ("queue_timeout_seconds", timeout)):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"admission.{name} must be a finite nonnegative number")
    return concurrency, float(interval), float(timeout)


class ProviderAdmission:
    def __init__(self, options: Any = None):
        self.maximum, self.interval, self.timeout = admission_options(options)
        self.condition = threading.Condition()
        self.active = 0
        self.next_dispatch = 0.0
        self.cooldowns: dict[tuple[str, str] | None, float] = {}

    def defer(self, key: tuple[str, str] | None, seconds: float) -> None:
        with self.condition:
            self.cooldowns[key] = max(self.cooldowns.get(key, 0.0), time.monotonic() + seconds)
            self.condition.notify_all()

    def acquire(self, key: tuple[str, str]) -> None:
        deadline = time.monotonic() + self.timeout
        with self.condition:
            while True:
                now = time.monotonic()
                self.cooldowns = {k: until for k, until in self.cooldowns.items() if until > now}
                ready = max(self.next_dispatch, self.cooldowns.get(None, 0.0), self.cooldowns.get(key, 0.0))
                if ready > deadline or (now >= deadline and (ready > now or self.active >= self.maximum)):
                    raise AdmissionDelayed(max(ready - now, 0.1))
                if self.active < self.maximum and ready <= now:
                    self.active += 1
                    self.next_dispatch = now + self.interval
                    return
                self.condition.wait(min(deadline - now, ready - now) if ready > now else deadline - now)

    def release(self) -> None:
        with self.condition:
            self.active -= 1
            self.condition.notify_all()
