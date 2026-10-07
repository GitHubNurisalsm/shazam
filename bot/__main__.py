"""Точка входа: python -m bot"""

import asyncio
import contextlib
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from pydantic import ValidationError

from bot import texts
from bot.config import Settings, get_settings
from bot.db.engine import create_engine, create_sessionmaker
from bot.dispatcher import build_dispatcher
from bot.services import audio
from bot.services.recognition.audd import AuddRecognizer
from bot.services.recognition.manager import RecognitionManager
from bot.services.recognition.shazam import ShazamRecognizer

logger = logging.getLogger("bot")


def setup_logging(level: str, log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        log_dir / "bot.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    stream_handler = logging.StreamHandler()
    for handler in (file_handler, stream_handler):
        handler.setFormatter(formatter)
    logging.basicConfig(level=level.upper(), handlers=[file_handler, stream_handler], force=True)


async def set_commands(bot: Bot, settings: Settings) -> None:
    user_commands = [
        BotCommand(command="start", description=texts.COMMAND_START),
        BotCommand(command="help", description=texts.COMMAND_HELP),
        BotCommand(command="history", description=texts.COMMAND_HISTORY),
    ]
    await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    admin_commands = [*user_commands, BotCommand(command="stats", description=texts.COMMAND_STATS)]
    for admin_id in settings.admin_ids:
        try:
            await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            # Админ ещё не писал боту — Telegram не знает такой чат.
            logger.warning("Cannot set admin commands for %s", admin_id)


def build_recognizer(settings: Settings) -> RecognitionManager:
    fallback = None
    if settings.audd_token is not None:
        fallback = AuddRecognizer(settings.audd_token.get_secret_value())
    else:
        logger.info("AUDD_TOKEN is empty: fallback provider disabled")
    return RecognitionManager(primary=ShazamRecognizer(), fallback=fallback)


async def main() -> None:
    try:
        settings = get_settings()
    except ValidationError as exc:
        logging.basicConfig(level=logging.INFO)
        logger.error("Invalid configuration (.env): %s", exc)
        sys.exit(1)

    setup_logging(settings.log_level, settings.log_dir)

    missing = audio.check_ffmpeg()
    if missing:
        logger.error("%s not found in PATH. Install ffmpeg and restart.", ", ".join(missing))
        sys.exit(1)

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    audio.cleanup_stale(settings.tmp_dir)

    engine = create_engine(settings.database_url)
    sessionmaker = create_sessionmaker(engine)
    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher(bot, settings, sessionmaker, build_recognizer(settings))

    try:
        await set_commands(bot, settings)
        logger.info("Bot started")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await engine.dispose()
        logger.info("Bot stopped")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
