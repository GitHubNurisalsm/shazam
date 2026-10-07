"""Сквозные сценарии: апдейт проходит через настоящий Dispatcher, middlewares и обработчики.

Telegram подменён FakeSession (запросы записываются, сеть не используется),
провайдер распознавания — заглушкой.
"""

import itertools
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageText,
    GetFile,
    GetMe,
    SendChatAction,
    SendMessage,
    SendPhoto,
    TelegramMethod,
)
from aiogram.types import (
    CallbackQuery,
    Chat,
    Document,
    File,
    Message,
    Update,
    User,
    Voice,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot import texts
from bot.config import Settings
from bot.db.models import Base, Recognition
from bot.db.models import User as DbUser
from bot.dispatcher import build_dispatcher
from bot.services.recognition.base import RecognitionError, TrackResult
from bot.services.recognition.manager import RecognitionManager
from tests.conftest import make_media, requires_ffmpeg

pytestmark = requires_ffmpeg

BOT_ID = 42
ADMIN_ID = 999
USER_ID = 1
TRACK = TrackResult(
    title="Blinding Lights",
    artist="The Weeknd",
    cover_url="https://img/cover.jpg",
    links={"shazam": "https://shazam/x"},
    provider="shazam",
    key="1",
)


class FakeSession(BaseSession):
    def __init__(self, media: bytes) -> None:
        super().__init__()
        self.media = media
        self.requests: list[TelegramMethod[Any]] = []
        self._ids = itertools.count(1000)

    async def close(self) -> None:
        pass

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        self.requests.append(method)
        if isinstance(method, GetMe):
            return User(id=BOT_ID, is_bot=True, first_name="Bot", username="songfinder_bot")
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u", file_path="voice/f.ogg")
        if isinstance(method, SendMessage | SendPhoto | EditMessageText):
            chat_id = int(method.chat_id or 0)
            return Message(
                message_id=next(self._ids),
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type="private"),
                from_user=User(id=BOT_ID, is_bot=True, first_name="Bot"),
                text=getattr(method, "text", None),
            ).as_(bot)
        return True

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield self.media

    def sent(self, *types: type) -> list[Any]:
        return [r for r in self.requests if isinstance(r, types)]

    def texts(self) -> list[str]:
        return [r.text for r in self.sent(SendMessage, EditMessageText)]


class StubRecognizer:
    name = "shazam"

    def __init__(self, *outcomes: TrackResult | Exception | None) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def recognize(self, path: Path) -> TrackResult | None:
        self.calls += 1
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Env:
    def __init__(
        self,
        bot: Bot,
        dp: Dispatcher,
        session: FakeSession,
        recognizer: StubRecognizer,
        db: async_sessionmaker[AsyncSession],
        tmp_dir: Path,
    ) -> None:
        self.bot, self.dp, self.session, self.recognizer, self.db = bot, dp, session, recognizer, db
        self.tmp_dir = tmp_dir
        self._update_ids = itertools.count(1)

    def message(self, user_id: int = USER_ID, chat_type: str = "private", **kwargs: Any) -> Message:
        chat_id = user_id if chat_type == "private" else -100
        return Message(
            message_id=next(self._update_ids),
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type=chat_type),
            from_user=User(id=user_id, is_bot=False, first_name="Name", username="nick"),
            **kwargs,
        )

    async def feed(self, message: Message | None = None, **update: Any) -> None:
        if message is not None:
            update["message"] = message
        await self.dp.feed_update(self.bot, Update(update_id=next(self._update_ids), **update))

    async def voice(self, file_id: str = "v1", size: int = 1000, user_id: int = USER_ID) -> None:
        voice = Voice(file_id=file_id, file_unique_id=file_id, duration=6, file_size=size)
        await self.feed(self.message(user_id=user_id, voice=voice))

    async def recognitions(self) -> list[Recognition]:
        async with self.db() as s:
            return list((await s.scalars(select(Recognition).order_by(Recognition.id))).all())


@pytest.fixture
async def make_env(tmp_path: Path) -> AsyncGenerator[Any, None]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    media = make_media(tmp_path / "sample.ogg", "-c:a", "libopus", seconds=6).read_bytes()
    bots: list[Bot] = []

    def factory(*outcomes: TrackResult | Exception | None, **overrides: Any) -> Env:
        settings = Settings(
            _env_file=None,
            bot_token="42:TEST",
            admin_ids=[ADMIN_ID],
            tmp_dir=tmp_path / "work",
            **overrides,
        )
        fake = FakeSession(media)
        bot = Bot("42:TEST", session=fake, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        bots.append(bot)
        recognizer = StubRecognizer(*(outcomes or (TRACK,)))
        manager = RecognitionManager(recognizer, None, retry_delay=0)
        db = async_sessionmaker(engine, expire_on_commit=False)
        dp = build_dispatcher(bot, settings, db, manager)
        return Env(bot, dp, fake, recognizer, db, settings.tmp_dir)

    yield factory
    await engine.dispose()


async def test_voice_is_recognized_and_card_sent(make_env: Any) -> None:
    env: Env = make_env()

    await env.voice()

    assert env.session.texts()[0] == texts.LISTENING
    [photo] = env.session.sent(SendPhoto)
    assert photo.photo == TRACK.cover_url
    assert "Blinding Lights" in (photo.caption or "")
    assert photo.reply_markup is not None
    assert env.session.sent(DeleteMessage), "«Слушаю…» должно удаляться после отправки фото"
    [row] = await env.recognitions()
    assert (row.success, row.provider, row.media_type, row.title) == (
        True,
        "shazam",
        "voice",
        "Blinding Lights",
    )
    assert list(env.tmp_dir.iterdir()) == [], "временные файлы должны удаляться"


async def test_same_file_is_served_from_cache(make_env: Any) -> None:
    env: Env = make_env()

    await env.voice("a")
    await env.voice("b")  # те же байты — тот же SHA-256

    assert env.recognizer.calls == 1
    assert len(env.session.sent(SendPhoto)) == 2
    assert len(await env.recognitions()) == 2


async def test_not_found_edits_status_and_records_failure(make_env: Any) -> None:
    env: Env = make_env(None)

    await env.voice()

    assert env.session.texts() == [texts.LISTENING, texts.NOT_FOUND]
    [row] = await env.recognitions()
    assert row.success is False and row.error is None


async def test_provider_outage_reports_unavailable_and_alerts_admin(make_env: Any) -> None:
    env: Env = make_env(RecognitionError("down"))

    await env.voice()

    assert texts.SERVICE_UNAVAILABLE in env.session.texts()
    alerts = [m for m in env.session.sent(SendMessage) if m.chat_id == ADMIN_ID]
    assert len(alerts) == 1
    [row] = await env.recognitions()
    assert (row.success, row.error) == (False, "service_unavailable")


async def test_file_over_limit_is_rejected_without_download(make_env: Any) -> None:
    env: Env = make_env()

    await env.voice(size=20 * 1024 * 1024 + 1)

    assert env.session.texts() == [texts.FILE_TOO_LARGE.format(max_mb=20)]
    assert env.session.sent(GetFile) == []
    assert env.recognizer.calls == 0


async def test_sixth_request_per_minute_gets_rate_limit(make_env: Any) -> None:
    env: Env = make_env(None)

    for i in range(6):
        await env.voice(f"v{i}")

    assert env.session.texts()[-1].startswith("Слишком много запросов. Попробуйте через ")
    assert len(await env.recognitions()) == 5


async def test_daily_limit_is_enforced_from_database(make_env: Any) -> None:
    env: Env = make_env(None, daily_limit=2, rate_limit_per_minute=100)

    for i in range(3):
        await env.voice(f"v{i}")

    assert env.session.texts()[-1] == texts.DAILY_LIMIT.format(limit=2)
    assert len(await env.recognitions()) == 2


async def test_unsupported_document_is_rejected(make_env: Any) -> None:
    env: Env = make_env()
    document = Document(file_id="d", file_unique_id="d", mime_type="application/pdf")

    await env.feed(env.message(document=document))

    assert env.session.texts() == [texts.UNSUPPORTED_FORMAT]
    assert env.session.sent(GetFile) == []


async def test_group_media_without_mention_is_ignored(make_env: Any) -> None:
    env: Env = make_env()
    voice = Voice(file_id="g", file_unique_id="g", duration=6, file_size=1000)

    await env.feed(env.message(chat_type="group", voice=voice))

    assert env.session.sent(SendMessage, SendPhoto) == []
    assert env.recognizer.calls == 0


async def test_banned_user_is_ignored(make_env: Any) -> None:
    env: Env = make_env()
    await env.feed(env.message(text="/start"))
    async with env.db() as s:
        await s.execute(update(DbUser).values(is_banned=True))
        await s.commit()
    env.session.requests.clear()

    await env.voice()

    assert env.session.sent(SendMessage, SendPhoto, GetFile) == []


async def test_text_gets_hint_and_stats_is_admin_only(make_env: Any) -> None:
    env: Env = make_env()

    await env.feed(env.message(text="какая это песня?"))
    await env.feed(env.message(text="/stats"))
    await env.feed(env.message(user_id=ADMIN_ID, text="/stats"))

    hint, non_admin, admin = env.session.texts()
    assert hint == non_admin == texts.SEND_MEDIA_HINT
    assert admin.startswith(texts.STATS_HEADER)


async def test_history_buttons_work_only_for_owner(make_env: Any) -> None:
    env: Env = make_env(
        *(TrackResult(title=f"T{i}", artist="A", provider="shazam") for i in range(11)),
        rate_limit_per_minute=100,
    )
    async with env.db() as s:
        for i in range(11):
            s.add(
                Recognition(
                    user_id=USER_ID,
                    media_type="voice",
                    file_hash=str(i),
                    success=True,
                    title=f"T{i}",
                    artist="A",
                    duration_ms=1,
                )
            )
        s.add(DbUser(id=USER_ID, first_name="Name"))
        await s.commit()

    await env.feed(env.message(text="/history"))
    [history] = env.session.sent(SendMessage)
    assert "стр. 1 из 2" in history.text
    next_button = history.reply_markup.inline_keyboard[0][0]

    def click(user_id: int) -> CallbackQuery:
        return CallbackQuery(
            id=str(user_id),
            chat_instance="c",
            data=next_button.callback_data,
            from_user=User(id=user_id, is_bot=False, first_name="X"),
            message=env.message(text=history.text),
        )

    await env.feed(callback_query=click(USER_ID + 1))
    await env.feed(callback_query=click(USER_ID))

    stranger, _owner = env.session.sent(AnswerCallbackQuery)
    assert stranger.show_alert is True
    [edited] = env.session.sent(EditMessageText)
    assert "стр. 2 из 2" in edited.text


async def test_chat_action_is_sent_while_recognizing(make_env: Any) -> None:
    env: Env = make_env()
    await env.voice()
    assert any(a.action == "typing" for a in env.session.sent(SendChatAction))
