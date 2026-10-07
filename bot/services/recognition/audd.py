from pathlib import Path
from typing import Any

import aiohttp

from bot.services.recognition.base import RecognitionError, TrackResult, clean

AUDD_URL = "https://api.audd.io/"


def _apple_cover(apple: dict[str, Any]) -> str | None:
    template = clean((apple.get("artwork") or {}).get("url"))
    if not template:
        return None
    return template.replace("{w}", "600").replace("{h}", "600")


def _spotify_cover(spotify: dict[str, Any]) -> str | None:
    images = (spotify.get("album") or {}).get("images") or []
    if images and isinstance(images[0], dict):
        return clean(images[0].get("url"))
    return None


def parse_audd_response(data: object) -> TrackResult | None:
    """status=success и result=null — «не найдено»; status=error — сбой провайдера."""
    if not isinstance(data, dict):
        raise RecognitionError("unexpected audd response type")
    if data.get("status") != "success":
        error = data.get("error") or {}
        code = error.get("error_code") if isinstance(error, dict) else None
        raise RecognitionError(f"audd error code={code}")
    result = data.get("result")
    if not result:
        return None
    if not isinstance(result, dict):
        raise RecognitionError("unexpected audd result type")
    title = clean(result.get("title"))
    artist = clean(result.get("artist"))
    if not title or not artist:
        raise RecognitionError("audd result without title or artist")

    track = TrackResult(title=title, artist=artist, provider="audd")
    try:
        track.album = clean(result.get("album"))
        release_date = clean(result.get("release_date"))
        track.year = release_date[:4] if release_date else None
        apple = result.get("apple_music") or {}
        spotify = result.get("spotify") or {}
        genres = apple.get("genreNames") or []
        track.genre = clean(genres[0]) if genres else None
        track.cover_url = _apple_cover(apple) or _spotify_cover(spotify)
        if apple_url := clean(apple.get("url")):
            track.links["apple_music"] = apple_url
        if spotify_url := clean((spotify.get("external_urls") or {}).get("spotify")):
            track.links["spotify"] = spotify_url
    except (AttributeError, TypeError, ValueError, IndexError):
        pass  # необязательные поля не должны ронять разбор
    return track


class AuddRecognizer:
    name = "audd"

    def __init__(self, token: str, timeout: float = 15.0, url: str = AUDD_URL) -> None:
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._url = url

    async def recognize(self, path: Path) -> TrackResult | None:
        form = aiohttp.FormData()
        form.add_field("api_token", self._token)
        form.add_field("return", "apple_music,spotify")
        try:
            with path.open("rb") as file:
                form.add_field("file", file, filename=path.name, content_type="audio/mpeg")
                async with (
                    aiohttp.ClientSession(timeout=self._timeout) as session,
                    session.post(self._url, data=form) as response,
                ):
                    response.raise_for_status()
                    data = await response.json(content_type=None)
        except TimeoutError as exc:
            raise RecognitionError("audd timeout") from exc
        except (aiohttp.ClientError, ValueError) as exc:
            # str(exc) у aiohttp не содержит тела запроса, токен в лог не попадёт.
            raise RecognitionError(f"audd request failed: {type(exc).__name__}") from exc
        return parse_audd_response(data)
