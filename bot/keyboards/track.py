from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.formatter import track_buttons
from bot.services.recognition.base import TrackResult


def track_keyboard(track: TrackResult) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, url in track_buttons(track):
        builder.button(text=label, url=url)
    builder.adjust(2)
    return builder.as_markup()
