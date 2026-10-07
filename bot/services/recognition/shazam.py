import asyncio
import logging
from pathlib import Path
from typing import Any

from bot.services.recognition.base import RecognitionError, TrackResult, clean

logger = logging.getLogger(__name__)


def _apple_music_url(track: dict[str, Any]) -> str | None:
    hub = track.get("hub") or {}
    actions: list[Any] = list(hub.get("actions") or [])
    for option in hub.get("options") or []:
        if isinstance(option, dict):
            actions.extend(option.get("actions") or [])
    for action in actions:
        uri = action.get("uri") if isinstance(action, dict) else None
        if isinstance(uri, str) and uri.startswith("https://music.apple.com/"):
            return uri
    return None


def _metadata(track: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for section in track.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for item in section.get("metadata") or []:
            if isinstance(item, dict) and item.get("title") and item.get("text"):
                result.setdefault(str(item["title"]).lower(), str(item["text"]))
    return result


def parse_shazam_response(data: object) -> TrackResult | None:
    """Разбирает ответ ShazamIO.

    None — трек не найден; RecognitionError — неожиданный формат ответа.
    Отсутствие необязательных полей не считается ошибкой.
    """
    if not isinstance(data, dict):
        raise RecognitionError(f"unexpected shazam response type: {type(data).__name__}")
    track = data.get("track")
    if not track:
        return None
    if not isinstance(track, dict):
        raise RecognitionError("unexpected shazam track type")
    title = clean(track.get("title"))
    artist = clean(track.get("subtitle"))
    if not title or not artist:
        raise RecognitionError("shazam track without title or subtitle")

    result = TrackResult(title=title, artist=artist, provider="shazam", key=clean(track.get("key")))
    try:
        images = track.get("images") or {}
        result.cover_url = clean(images.get("coverarthq")) or clean(images.get("coverart"))
        result.genre = clean((track.get("genres") or {}).get("primary"))
        meta = _metadata(track)
        result.album = clean(meta.get("album"))
        result.year = clean(meta.get("released"))
        if url := clean(track.get("url")):
            result.links["shazam"] = url
        if apple := _apple_music_url(track):
            result.links["apple_music"] = apple
    except (AttributeError, TypeError, ValueError):
        logger.warning("Failed to parse optional shazam fields", exc_info=True)
    return result


class ShazamRecognizer:
    name = "shazam"

    def __init__(self, client: Any = None, timeout: float = 15.0) -> None:
        if client is None:
            from shazamio import Shazam

            client = Shazam()
        self._client = client
        self._timeout = timeout

    async def recognize(self, path: Path) -> TrackResult | None:
        try:
            data = await asyncio.wait_for(self._client.recognize(str(path)), self._timeout)
        except TimeoutError as exc:
            raise RecognitionError("shazam timeout") from exc
        except RecognitionError:
            raise
        except Exception as exc:
            raise RecognitionError(f"shazam error: {exc!r}") from exc
        return parse_shazam_response(data)
