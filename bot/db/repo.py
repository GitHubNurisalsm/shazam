import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Recognition, User, utcnow
from bot.services.recognition.base import TrackResult

CACHE_TTL = timedelta(hours=24)
HISTORY_PAGE_SIZE = 10


def _cut(value: str | None, length: int) -> str | None:
    return value[:length] if value else value


async def upsert_user(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    first_name: str,
    language_code: str | None,
) -> User:
    """Регистрирует пользователя или обновляет его данные и last_seen_at.

    INSERT ... ON CONFLICT безопасен при одновременных первых запросах одного пользователя.
    """
    now = utcnow()
    values: dict[str, Any] = {
        "id": user_id,
        "username": _cut(username, 64),
        "first_name": _cut(first_name, 128) or "",
        "language_code": _cut(language_code, 8),
        "created_at": now,
        "last_seen_at": now,
        "is_banned": False,
    }
    update = {k: values[k] for k in ("username", "first_name", "language_code", "last_seen_at")}
    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert(User).values(**values).on_conflict_do_update(index_elements=["id"], set_=update)
    await session.execute(stmt)
    await session.commit()
    user = await session.get(User, user_id, populate_existing=True)
    assert user is not None
    return user


async def count_recent(session: AsyncSession, user_id: int, period: timedelta) -> int:
    since = utcnow() - period
    stmt = select(func.count(Recognition.id)).where(
        Recognition.user_id == user_id, Recognition.created_at >= since
    )
    return int(await session.scalar(stmt) or 0)


async def find_cached(session: AsyncSession, file_hash: str) -> TrackResult | None:
    """Успешный результат по тому же файлу не старше 24 часов (FR-15)."""
    stmt = (
        select(Recognition)
        .where(
            Recognition.file_hash == file_hash,
            Recognition.success.is_(True),
            Recognition.created_at >= utcnow() - CACHE_TTL,
        )
        .order_by(Recognition.created_at.desc(), Recognition.id.desc())
        .limit(1)
    )
    row = await session.scalar(stmt)
    if row is None or not row.title or not row.artist:
        return None
    links: dict[str, str] = {}
    if row.links_json:
        try:
            data = json.loads(row.links_json)
            meta = data if isinstance(data, dict) else {}
            links = {k: v for k, v in (meta.get("links") or {}).items() if isinstance(v, str)}
        except ValueError:
            meta = {}
    else:
        meta = {}
    return TrackResult(
        title=row.title,
        artist=row.artist,
        album=meta.get("album"),
        year=meta.get("year"),
        genre=meta.get("genre"),
        cover_url=row.cover_url,
        links=links,
        provider=row.provider or "",
        key=row.shazam_key,
    )


async def add_recognition(
    session: AsyncSession,
    *,
    user_id: int,
    media_type: str,
    file_hash: str,
    track: TrackResult | None,
    duration_ms: int,
    error: str | None = None,
) -> Recognition:
    links_json = None
    if track is not None:
        # В links_json храним и ссылки, и метаданные карточки, чтобы кэш отдавал её целиком.
        links_json = json.dumps(
            {"links": track.links, "album": track.album, "year": track.year, "genre": track.genre},
            ensure_ascii=False,
        )
    row = Recognition(
        user_id=user_id,
        media_type=media_type,
        file_hash=file_hash,
        success=track is not None,
        provider=track.provider if track else None,
        title=_cut(track.title, 256) if track else None,
        artist=_cut(track.artist, 256) if track else None,
        shazam_key=_cut(track.key, 32) if track else None,
        cover_url=track.cover_url if track else None,
        links_json=links_json,
        duration_ms=duration_ms,
        error=_cut(error, 256),
    )
    session.add(row)
    await session.commit()
    return row


@dataclass
class HistoryPage:
    items: list[Recognition]
    page: int
    pages: int


async def get_history(session: AsyncSession, user_id: int, page: int) -> HistoryPage:
    """Найденные треки пользователя, по HISTORY_PAGE_SIZE на страницу, новые сверху."""
    condition = (Recognition.user_id == user_id) & Recognition.success.is_(True)
    total = int(await session.scalar(select(func.count(Recognition.id)).where(condition)) or 0)
    pages = max(1, -(-total // HISTORY_PAGE_SIZE))
    page = min(max(page, 1), pages)
    stmt = (
        select(Recognition)
        .where(condition)
        .order_by(Recognition.created_at.desc(), Recognition.id.desc())
        .offset((page - 1) * HISTORY_PAGE_SIZE)
        .limit(HISTORY_PAGE_SIZE)
    )
    items = list((await session.scalars(stmt)).all())
    return HistoryPage(items=items, page=page, pages=pages)


@dataclass
class PeriodStats:
    users: int
    new_users: int
    requests: int
    successful: int

    @property
    def success_rate(self) -> float:
        return round(100 * self.successful / self.requests, 1) if self.requests else 0.0


async def period_stats(session: AsyncSession, since: datetime | None) -> PeriodStats:
    rec_filter = [Recognition.created_at >= since] if since else []
    user_filter = [User.created_at >= since] if since else []
    requests, successful, users = (
        await session.execute(
            select(
                func.count(Recognition.id),
                func.count(Recognition.id).filter(Recognition.success.is_(True)),
                func.count(func.distinct(Recognition.user_id)),
            ).where(*rec_filter)
        )
    ).one()
    new_users = await session.scalar(select(func.count(User.id)).where(*user_filter))
    # За всё время показываем всех зарегистрированных, за период — активных.
    active_users = new_users if since is None else users
    return PeriodStats(
        users=int(active_users or 0),
        new_users=int(new_users or 0),
        requests=int(requests or 0),
        successful=int(successful or 0),
    )


async def top_tracks(session: AsyncSession, limit: int = 10) -> list[tuple[str, str, int]]:
    count = func.count(Recognition.id).label("cnt")
    stmt = (
        select(Recognition.title, Recognition.artist, count)
        .where(Recognition.success.is_(True))
        .group_by(Recognition.title, Recognition.artist)
        .order_by(count.desc(), Recognition.title)
        .limit(limit)
    )
    return [(str(t), str(a), int(c)) for t, a, c in (await session.execute(stmt)).all()]
