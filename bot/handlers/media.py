"""Приём voice, audio, video, video_note и document: скачивание, распознавание, карточка."""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Filter
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.config import Settings
from bot.db import repo
from bot.keyboards.track import track_keyboard
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.services import audio
from bot.services.formatter import format_track
from bot.services.limits import RateLimiter, UserLocks
from bot.services.recognition.base import ServiceUnavailableError, TrackResult
from bot.services.recognition.manager import RecognitionManager
from bot.utils import AdminAlerts, tg_retry

logger = logging.getLogger(__name__)

SUPPORTED_MIME_PREFIXES = ("audio/", "video/")
DAY = timedelta(hours=24)


@dataclass(frozen=True)
class MediaInfo:
    media_type: str  # voice | audio | video | video_note | document
    file_id: str
    file_size: int | None
    supported: bool = True


def extract_media(message: Message) -> MediaInfo | None:
    if message.voice:
        return MediaInfo("voice", message.voice.file_id, message.voice.file_size)
    if message.audio:
        return MediaInfo("audio", message.audio.file_id, message.audio.file_size)
    if message.video:
        return MediaInfo("video", message.video.file_id, message.video.file_size)
    if message.video_note:
        return MediaInfo("video_note", message.video_note.file_id, message.video_note.file_size)
    if message.animation:
        # GIF/анимации приходят и как document, но звука в них нет.
        return MediaInfo("document", message.animation.file_id, None, supported=False)
    if message.document:
        mime = (message.document.mime_type or "").lower()
        return MediaInfo(
            "document",
            message.document.file_id,
            message.document.file_size,
            supported=mime.startswith(SUPPORTED_MIME_PREFIXES),
        )
    return None


class MediaFilter(Filter):
    """Пропускает медиа-сообщения и передаёт в обработчик `media: MediaInfo`.

    В группах реагирует только на ответ на сообщение бота или упоминание бота (FR-18).
    """

    async def __call__(self, message: Message, bot: Bot) -> bool | dict[str, Any]:
        media = extract_media(message)
        if media is None:
            return False
        if message.chat.type != ChatType.PRIVATE and not await _addressed_to_bot(message, bot):
            return False
        return {"media": media}


async def _addressed_to_bot(message: Message, bot: Bot) -> bool:
    me = await bot.me()
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == me.id:
        return True
    if not me.username:
        return False
    return f"@{me.username.lower()}" in (message.caption or "").lower()


async def handle_media(
    message: Message,
    bot: Bot,
    media: MediaInfo,
    session: AsyncSession,
    settings: Settings,
    recognizer: RecognitionManager,
    user_locks: UserLocks,
    recognition_semaphore: asyncio.Semaphore,
    admin_alerts: AdminAlerts,
) -> None:
    assert message.from_user is not None
    user_id = message.from_user.id

    if not media.supported:
        logger.warning("Unsupported media from user %s: %s", user_id, media.media_type)
        await message.reply(texts.UNSUPPORTED_FORMAT)
        return
    if media.file_size is not None and media.file_size > settings.max_file_bytes:
        await message.reply(texts.FILE_TOO_LARGE.format(max_mb=settings.max_file_mb))
        return

    queued = user_locks.is_busy(user_id)
    status = await message.reply(texts.QUEUED if queued else texts.LISTENING)

    async with user_locks.hold(user_id):
        started = time.monotonic()
        daily_used = await repo.count_recent(session, user_id, DAY)
        if daily_used >= settings.daily_limit:
            await _edit(status, texts.DAILY_LIMIT.format(limit=settings.daily_limit))
            return
        async with ChatActionSender.typing(chat_id=message.chat.id, bot=bot):
            await _process(
                message,
                bot,
                status,
                media,
                session,
                settings,
                recognizer,
                recognition_semaphore,
                admin_alerts,
                started,
            )


async def _process(
    message: Message,
    bot: Bot,
    status: Message,
    media: MediaInfo,
    session: AsyncSession,
    settings: Settings,
    recognizer: RecognitionManager,
    semaphore: asyncio.Semaphore,
    admin_alerts: AdminAlerts,
    started: float,
) -> None:
    assert message.from_user is not None
    user_id = message.from_user.id
    file_hash: str | None = None
    track: TrackResult | None = None
    error: str | None = None
    reply_text: str | None = None
    from_cache = False

    try:
        async with semaphore, audio.temp_workspace(settings.tmp_dir) as workspace:
            source = workspace / "input"
            try:
                await bot.download(media.file_id, destination=source)
            except TelegramBadRequest as exc:
                if "too big" in str(exc).lower():
                    await _edit(status, texts.FILE_TOO_LARGE.format(max_mb=settings.max_file_mb))
                    return
                raise
            file_hash = await audio.sha256_file(source)
            track = await repo.find_cached(session, file_hash)
            from_cache = track is not None
            if track is None:
                fragment = await audio.prepare_fragment(
                    source, workspace, settings.fragment_seconds
                )
                track = await recognizer.recognize(fragment)
            if track is None:
                reply_text = texts.NOT_FOUND
    except audio.TooShortError:
        error, reply_text = "too_short", texts.TOO_SHORT
    except audio.NoAudioStreamError:
        logger.info("No audio stream in media from user %s", user_id)
        error, reply_text = "no_audio", texts.NO_AUDIO
    except audio.UnsupportedFormatError as exc:
        logger.warning("Unsupported format from user %s: %s", user_id, exc)
        error, reply_text = "unsupported_format", texts.UNSUPPORTED_FORMAT
    except ServiceUnavailableError as exc:
        logger.error("Recognition service unavailable: %s", exc)
        error, reply_text = "service_unavailable", texts.SERVICE_UNAVAILABLE
        await admin_alerts.notify(texts.ADMIN_ALERT_PROVIDERS_DOWN)
    except Exception as exc:
        logger.exception("Failed to process media from user %s", user_id)
        error, reply_text = f"internal: {type(exc).__name__}", texts.GENERIC_ERROR

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "recognition user=%s media=%s provider=%s found=%s cache=%s duration_ms=%s error=%s",
        user_id,
        media.media_type,
        track.provider if track else None,
        track is not None,
        from_cache,
        duration_ms,
        error,
    )
    if file_hash is not None:
        try:
            await repo.add_recognition(
                session,
                user_id=user_id,
                media_type=media.media_type,
                file_hash=file_hash,
                track=track,
                duration_ms=duration_ms,
                error=error,
            )
        except Exception:
            logger.exception("Failed to save recognition for user %s", user_id)
            await session.rollback()

    if track is not None:
        await _send_track(message, status, track)
    else:
        await _edit(status, reply_text or texts.GENERIC_ERROR)


async def _send_track(message: Message, status: Message, track: TrackResult) -> None:
    caption = format_track(track)
    keyboard = track_keyboard(track)
    if track.cover_url:
        cover_url = track.cover_url
        try:
            await tg_retry(
                lambda: message.reply_photo(cover_url, caption=caption, reply_markup=keyboard)
            )
        except TelegramBadRequest:
            logger.warning("Failed to send cover %s, falling back to text", cover_url)
        else:
            # Фото нельзя получить редактированием текстового сообщения — удаляем «Слушаю…».
            with contextlib.suppress(TelegramBadRequest):
                await status.delete()
            return
    await _edit(status, caption, keyboard)


async def _edit(status: Message, text: str, keyboard: Any = None) -> None:
    try:
        await tg_retry(lambda: status.edit_text(text, reply_markup=keyboard))
    except TelegramBadRequest:
        logger.warning("Failed to edit status message, sending new one", exc_info=True)
        await tg_retry(lambda: status.answer(text, reply_markup=keyboard))


def create_router(limiter: RateLimiter) -> Router:
    router = Router(name="media")
    router.message.middleware(ThrottlingMiddleware(limiter))
    router.message.register(handle_media, MediaFilter())
    return router
