from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class TrackResult:
    title: str
    artist: str
    album: str | None = None
    year: str | None = None
    genre: str | None = None
    cover_url: str | None = None
    links: dict[str, str] = field(default_factory=dict)  # {"shazam": url, "apple_music": url}
    provider: str = ""  # "shazam" | "audd"
    key: str | None = None  # ID трека у провайдера (shazam_key в БД)


class Recognizer(Protocol):
    name: str

    async def recognize(self, path: Path) -> TrackResult | None:
        """None — трек не найден. Исключение — сбой провайдера."""
        ...


class RecognitionError(Exception):
    """Сбой провайдера: сеть, таймаут, ошибка API или неожиданный формат ответа."""


class ServiceUnavailableError(Exception):
    """Ни один провайдер не смог обработать запрос."""


def clean(value: object) -> str | None:
    """Приводит необязательное поле ответа к непустой строке или None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None
