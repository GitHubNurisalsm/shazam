from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.db.models import Recognition, User, utcnow
from bot.services.recognition.base import TrackResult

TRACK = TrackResult(
    title="Blinding Lights",
    artist="The Weeknd",
    album="After Hours",
    year="2020",
    genre="Pop",
    cover_url="https://img/x.jpg",
    links={"shazam": "https://shazam/x"},
    provider="shazam",
    key="512345678",
)


async def _user(session: AsyncSession, user_id: int = 1) -> None:
    await repo.upsert_user(session, user_id, "nick", "Name", "ru")


async def _add(
    session: AsyncSession, user_id: int = 1, track: TrackResult | None = TRACK, file_hash: str = "h"
) -> Recognition:
    return await repo.add_recognition(
        session,
        user_id=user_id,
        media_type="voice",
        file_hash=file_hash,
        track=track,
        duration_ms=100,
    )


async def _age(session: AsyncSession, row: Recognition, delta: timedelta) -> None:
    await session.execute(
        update(Recognition).where(Recognition.id == row.id).values(created_at=utcnow() - delta)
    )
    await session.commit()


async def test_upsert_user_is_idempotent_and_updates_profile(session: AsyncSession) -> None:
    await repo.upsert_user(session, 1, "old", "Old", "ru")
    user = await repo.upsert_user(session, 1, "new", "New", "en")

    assert (user.username, user.first_name, user.language_code) == ("new", "New", "en")
    assert len((await session.scalars(select(User))).all()) == 1


async def test_upsert_keeps_ban_flag(session: AsyncSession) -> None:
    await _user(session)
    await session.execute(update(User).values(is_banned=True))
    await session.commit()

    user = await repo.upsert_user(session, 1, "nick", "Name", "ru")

    assert user.is_banned is True


async def test_cache_returns_full_card_for_same_file(session: AsyncSession) -> None:
    await _user(session)
    await _add(session, file_hash="abc")

    cached = await repo.find_cached(session, "abc")

    assert cached == TRACK


async def test_cache_ignores_failures_other_files_and_old_results(session: AsyncSession) -> None:
    await _user(session)
    await _add(session, track=None, file_hash="failed")
    old = await _add(session, file_hash="old")
    await _age(session, old, timedelta(hours=25))
    await _add(session, file_hash="other")

    assert await repo.find_cached(session, "failed") is None
    assert await repo.find_cached(session, "old") is None
    assert await repo.find_cached(session, "missing") is None


async def test_daily_count_uses_rolling_24h_window(session: AsyncSession) -> None:
    await _user(session)
    await _user(session, 2)
    for _ in range(3):
        await _add(session)
    await _add(session, user_id=2)
    old = await _add(session, track=None)
    await _age(session, old, timedelta(hours=25))

    assert await repo.count_recent(session, 1, timedelta(hours=24)) == 3


async def test_history_paginates_only_own_successful_tracks(session: AsyncSession) -> None:
    await _user(session)
    await _user(session, 2)
    for i in range(12):
        await _add(session, track=TrackResult(title=f"T{i}", artist="A", provider="shazam"))
    await _add(session, track=None)
    await _add(session, user_id=2)

    first = await repo.get_history(session, 1, 1)
    second = await repo.get_history(session, 1, 2)

    assert (first.page, first.pages, len(first.items)) == (1, 2, 10)
    assert first.items[0].title == "T11"  # новые сверху
    assert [r.title for r in second.items] == ["T1", "T0"]
    assert all(r.user_id == 1 and r.success for r in first.items + second.items)


async def test_history_page_out_of_range_is_clamped(session: AsyncSession) -> None:
    await _user(session)
    page = await repo.get_history(session, 1, 99)
    assert (page.page, page.pages, page.items) == (1, 1, [])


async def test_stats_and_top(session: AsyncSession) -> None:
    await _user(session)
    await _user(session, 2)
    await _add(session)
    await _add(session, user_id=2)
    await _add(session, track=TrackResult(title="Other", artist="X", provider="audd"))
    await _add(session, track=None)

    stats = await repo.period_stats(session, utcnow() - timedelta(days=1))
    total = await repo.period_stats(session, None)
    top = await repo.top_tracks(session)

    assert (stats.users, stats.requests, stats.successful, stats.success_rate) == (2, 4, 3, 75.0)
    assert total.users == 2
    assert top == [("Blinding Lights", "The Weeknd", 2), ("Other", "X", 1)]


async def test_long_values_are_truncated_to_column_size(session: AsyncSession) -> None:
    await _user(session)
    row = await _add(session, track=TrackResult(title="x" * 500, artist="y" * 500, key="k" * 100))
    lengths = (len(row.title or ""), len(row.artist or ""), len(row.shazam_key or ""))
    assert lengths == (256, 256, 32)
