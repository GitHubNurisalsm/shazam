import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from bot.services.recognition.audd import AuddRecognizer, parse_audd_response
from bot.services.recognition.base import (
    RecognitionError,
    ServiceUnavailableError,
    TrackResult,
)
from bot.services.recognition.manager import RecognitionManager
from bot.services.recognition.shazam import ShazamRecognizer, parse_shazam_response
from tests.conftest import load_sample

FRAGMENT = Path("fragment.mp3")


# --- Разбор ответов ---------------------------------------------------------


def test_parse_shazam_full_response() -> None:
    track = parse_shazam_response(load_sample("shazam_full.json"))

    assert track == TrackResult(
        title="Blinding Lights",
        artist="The Weeknd",
        album="After Hours",
        year="2020",
        genre="Pop",
        cover_url="https://is1-ssl.mzstatic.com/image/thumb/800x800cc.jpg",
        links={
            "shazam": "https://www.shazam.com/track/512345678/blinding-lights",
            "apple_music": "https://music.apple.com/us/album/blinding-lights/1499378108?i=1499378615",
        },
        provider="shazam",
        key="512345678",
    )


def test_parse_shazam_minimal_response_does_not_crash_on_broken_optional_fields() -> None:
    track = parse_shazam_response(load_sample("shazam_minimal.json"))

    assert track is not None
    assert (track.title, track.artist) == ("Song", "Artist")
    assert track.album is None and track.cover_url is None and track.links == {}


def test_parse_shazam_without_track_means_not_found() -> None:
    assert parse_shazam_response(load_sample("shazam_not_found.json")) is None


@pytest.mark.parametrize("data", [None, [], "html", {"track": "x"}, {"track": {"title": "T"}}])
def test_parse_shazam_unexpected_format_is_provider_error(data: Any) -> None:
    with pytest.raises(RecognitionError):
        parse_shazam_response(data)


def test_parse_audd_full_response() -> None:
    track = parse_audd_response(load_sample("audd_full.json"))

    assert track is not None
    assert track.provider == "audd"
    assert (track.title, track.artist, track.album, track.year) == (
        "Blinding Lights",
        "The Weeknd",
        "After Hours",
        "2020",
    )
    assert track.genre == "R&B/Soul"
    assert track.cover_url == "https://is1-ssl.mzstatic.com/image/thumb/600x600bb.jpg"
    assert track.links["spotify"] == "https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b"
    assert track.links["apple_music"].startswith("https://music.apple.com/")


def test_parse_audd_null_result_means_not_found() -> None:
    assert parse_audd_response(load_sample("audd_not_found.json")) is None


def test_parse_audd_error_status_is_provider_error() -> None:
    with pytest.raises(RecognitionError, match="900"):
        parse_audd_response(load_sample("audd_error.json"))


# --- Провайдеры без сети ----------------------------------------------------


class FakeShazamClient:
    def __init__(self, result: Any = None, exc: BaseException | None = None, delay: float = 0):
        self.result, self.exc, self.delay = result, exc, delay

    async def recognize(self, path: str) -> Any:
        await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.result


async def test_shazam_recognizer_parses_client_response() -> None:
    recognizer = ShazamRecognizer(client=FakeShazamClient(load_sample("shazam_full.json")))
    track = await recognizer.recognize(FRAGMENT)
    assert track is not None and track.title == "Blinding Lights"


async def test_shazam_recognizer_wraps_client_errors() -> None:
    recognizer = ShazamRecognizer(client=FakeShazamClient(exc=ConnectionError("down")))
    with pytest.raises(RecognitionError):
        await recognizer.recognize(FRAGMENT)


async def test_shazam_recognizer_timeout_is_provider_error() -> None:
    recognizer = ShazamRecognizer(client=FakeShazamClient(delay=1), timeout=0.01)
    with pytest.raises(RecognitionError, match="timeout"):
        await recognizer.recognize(FRAGMENT)


@pytest.fixture
async def audd_server() -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """Локальный сервер вместо api.audd.io; state управляет ответом."""
    state: dict[str, Any] = {"status": 200, "body": load_sample("audd_full.json"), "fields": {}}

    async def handler(request: web.Request) -> web.Response:
        form = await request.post()
        state["fields"] = {k: v for k, v in form.items() if isinstance(v, str)}
        state["has_file"] = "file" in form
        return web.json_response(state["body"], status=state["status"])

    app = web.Application()
    app.router.add_post("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    yield f"http://127.0.0.1:{port}/", state
    await runner.cleanup()


async def test_audd_recognizer_sends_token_file_and_return_fields(
    tmp_path: Path, audd_server: tuple[str, dict[str, Any]]
) -> None:
    url, state = audd_server
    fragment = tmp_path / "fragment.mp3"
    fragment.write_bytes(b"ID3fake")

    track = await AuddRecognizer("secret", url=url).recognize(fragment)

    assert track is not None and track.provider == "audd"
    assert state["fields"] == {"api_token": "secret", "return": "apple_music,spotify"}
    assert state["has_file"] is True


async def test_audd_recognizer_http_error_is_provider_error(
    tmp_path: Path, audd_server: tuple[str, dict[str, Any]]
) -> None:
    url, state = audd_server
    state["status"] = 502
    fragment = tmp_path / "fragment.mp3"
    fragment.write_bytes(b"ID3fake")

    with pytest.raises(RecognitionError) as info:
        await AuddRecognizer("secret", url=url).recognize(fragment)
    assert "secret" not in str(info.value)


# --- Выбор провайдера -------------------------------------------------------


class StubRecognizer:
    def __init__(self, name: str, *outcomes: TrackResult | Exception | None) -> None:
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = 0

    async def recognize(self, path: Path) -> TrackResult | None:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


SHAZAM_TRACK = TrackResult(title="A", artist="B", provider="shazam")
AUDD_TRACK = TrackResult(title="A", artist="B", provider="audd")


async def test_manager_returns_primary_result() -> None:
    primary, fallback = StubRecognizer("shazam", SHAZAM_TRACK), StubRecognizer("audd")
    manager = RecognitionManager(primary, fallback, retry_delay=0)

    assert await manager.recognize(FRAGMENT) is SHAZAM_TRACK
    assert fallback.calls == 0


async def test_manager_does_not_call_fallback_when_not_found() -> None:
    primary, fallback = StubRecognizer("shazam", None), StubRecognizer("audd", AUDD_TRACK)
    manager = RecognitionManager(primary, fallback, retry_delay=0)

    assert await manager.recognize(FRAGMENT) is None
    assert (primary.calls, fallback.calls) == (1, 0)


async def test_manager_retries_primary_once_before_fallback() -> None:
    primary = StubRecognizer("shazam", RecognitionError("net"), SHAZAM_TRACK)
    fallback = StubRecognizer("audd", AUDD_TRACK)
    manager = RecognitionManager(primary, fallback, retry_delay=0)

    assert await manager.recognize(FRAGMENT) is SHAZAM_TRACK
    assert (primary.calls, fallback.calls) == (2, 0)


async def test_manager_switches_to_fallback_after_two_failures() -> None:
    primary = StubRecognizer("shazam", RecognitionError("net"), TimeoutError())
    fallback = StubRecognizer("audd", AUDD_TRACK)
    manager = RecognitionManager(primary, fallback, retry_delay=0)

    assert await manager.recognize(FRAGMENT) is AUDD_TRACK
    assert (primary.calls, fallback.calls) == (2, 1)


async def test_manager_raises_service_unavailable_when_all_fail() -> None:
    primary = StubRecognizer("shazam", RecognitionError("a"), RecognitionError("b"))
    fallback = StubRecognizer("audd", RecognitionError("c"))
    manager = RecognitionManager(primary, fallback, retry_delay=0)

    with pytest.raises(ServiceUnavailableError):
        await manager.recognize(FRAGMENT)


async def test_manager_without_fallback_raises_service_unavailable() -> None:
    primary = StubRecognizer("shazam", RecognitionError("a"), RecognitionError("b"))
    manager = RecognitionManager(primary, None, retry_delay=0)

    with pytest.raises(ServiceUnavailableError):
        await manager.recognize(FRAGMENT)


async def test_manager_waits_one_second_before_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("bot.services.recognition.manager.asyncio.sleep", fake_sleep)
    primary = StubRecognizer("shazam", RecognitionError("net"), SHAZAM_TRACK)

    await RecognitionManager(primary).recognize(FRAGMENT)

    assert delays == [1.0]
