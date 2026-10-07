"""Ограничение частоты запросов и последовательная обработка файлов одного пользователя.

Состояние хранится в памяти процесса (в версии 2.0 — Redis), поэтому бот должен
работать в одном экземпляре.
"""

import asyncio
import contextlib
import math
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable


class RateLimiter:
    """Скользящее окно: не больше `limit` событий за `period` секунд на пользователя."""

    def __init__(
        self,
        limit: int,
        period: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.period = period
        self._clock = clock
        self._hits: dict[int, deque[float]] = {}

    def hit(self, user_id: int) -> int:
        """Регистрирует запрос. Возвращает 0, если он разрешён, иначе — секунды ожидания."""
        now = self._clock()
        self._gc(now)
        hits = self._hits.setdefault(user_id, deque())
        while hits and hits[0] <= now - self.period:
            hits.popleft()
        if len(hits) >= self.limit:
            return max(1, math.ceil(hits[0] + self.period - now))
        hits.append(now)
        return 0

    def _gc(self, now: float) -> None:
        # Не даём словарю расти бесконечно за счёт давно неактивных пользователей.
        if len(self._hits) < 10_000:
            return
        cutoff = now - self.period
        stale = [uid for uid, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for uid in stale:
            del self._hits[uid]


class UserLocks:
    """Пока файл пользователя обрабатывается, следующий ждёт своей очереди."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._waiters: dict[int, int] = {}

    def is_busy(self, user_id: int) -> bool:
        return user_id in self._locks

    @contextlib.asynccontextmanager
    async def hold(self, user_id: int) -> AsyncGenerator[None, None]:
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        self._waiters[user_id] = self._waiters.get(user_id, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._waiters[user_id] -= 1
            if self._waiters[user_id] == 0:
                del self._waiters[user_id]
                del self._locks[user_id]
