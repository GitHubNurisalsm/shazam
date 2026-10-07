import asyncio

from bot.services.limits import RateLimiter, UserLocks


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_sixth_request_per_minute_is_rejected_with_wait_time() -> None:
    clock = FakeClock()
    limiter = RateLimiter(limit=5, period=60, clock=clock)

    assert [limiter.hit(1) for _ in range(5)] == [0, 0, 0, 0, 0]
    clock.now += 15
    assert limiter.hit(1) == 45


def test_limit_is_per_user() -> None:
    limiter = RateLimiter(limit=1, period=60, clock=FakeClock())
    assert limiter.hit(1) == 0
    assert limiter.hit(2) == 0
    assert limiter.hit(1) > 0


def test_window_slides() -> None:
    clock = FakeClock()
    limiter = RateLimiter(limit=2, period=60, clock=clock)
    limiter.hit(1)
    clock.now += 30
    limiter.hit(1)
    clock.now += 31  # первый запрос вышел из окна
    assert limiter.hit(1) == 0
    assert limiter.hit(1) > 0


def test_rejected_requests_do_not_extend_the_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(limit=1, period=60, clock=clock)
    limiter.hit(1)
    for _ in range(10):
        limiter.hit(1)
    clock.now += 60
    assert limiter.hit(1) == 0


async def test_user_files_are_processed_sequentially() -> None:
    locks = UserLocks()
    active = 0
    max_active = 0

    async def job() -> None:
        nonlocal active, max_active
        async with locks.hold(1):
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(job() for _ in range(3)))

    assert max_active == 1
    assert not locks.is_busy(1)  # словарь блокировок не растёт


async def test_different_users_run_in_parallel() -> None:
    locks = UserLocks()
    started = asyncio.Event()

    async def first() -> None:
        async with locks.hold(1):
            started.set()
            await asyncio.sleep(0.05)

    async def second() -> bool:
        await started.wait()
        async with locks.hold(2):
            return locks.is_busy(1)

    _, overlapped = await asyncio.gather(first(), second())
    assert overlapped is True
