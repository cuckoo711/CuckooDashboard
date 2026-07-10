"""Small cache primitives used by dashboard services."""

from __future__ import annotations

import time
from typing import Any


class TTLCache:
    """Single-value TTL cache."""

    def __init__(self, ttl: float):
        self.ttl = ttl
        self.data: Any = None
        self.ts = 0.0

    def valid(self) -> bool:
        return self.data is not None and time.time() - self.ts < self.ttl

    def get(self):
        return self.data if self.valid() else None

    def set(self, data):
        self.data = data
        self.ts = time.time()
        return data

    def clear(self):
        self.data = None
        self.ts = 0.0
