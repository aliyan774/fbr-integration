"""Per-seller-NTN serialisation plus a global in-flight cap (single process, asyncio)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator


class ConcurrencyGate:
    def __init__(self, max_inflight_total: int, max_inflight_per_seller: int):
        self.max_total = max(1, int(max_inflight_total))
        self.max_per_seller = max(1, int(max_inflight_per_seller))
        self._global = asyncio.Semaphore(self.max_total)
        self._per_seller: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(self.max_per_seller)
        )
        self._inflight = 0

    @property
    def inflight(self) -> int:
        return self._inflight

    @asynccontextmanager
    async def acquire(self, seller_ntn: str) -> AsyncIterator[None]:
        """Wait for a global slot, then for the seller's slot (default 1 = PRAL calls per NTN serialised)."""
        async with self._global:
            async with self._per_seller[seller_ntn]:
                self._inflight += 1
                try:
                    yield
                finally:
                    self._inflight -= 1
