from html import escape
from urllib.parse import quote

from bot.services.recognition.base import TrackResult

# Подпись к фото в Telegram ограничена 1024 символами.
MAX_FIELD_LENGTH = 200


def _field(value: str) -> str:
    if len(value) > MAX_FIELD_LENGTH:
        value = value[: MAX_FIELD_LENGTH - 1] + "…"
    return escape(value)


def format_track(track: TrackResult) -> str:
    """Текст карточки трека (parse_mode=HTML)."""
    lines = [f"🎵 <b>{_field(track.title)}</b>", f"👤 {_field(track.artist)}"]
    album_line = " · ".join(_field(part) for part in (track.album, track.year) if part)
    if album_line:
        lines.append(f"💿 {album_line}")
    if track.genre:
        lines.append(f"🏷 {_field(track.genre)}")
    return "\n".join(lines)


def search_query(track: TrackResult) -> str:
    return f"{track.artist} {track.title}"


def track_buttons(track: TrackResult) -> list[tuple[str, str]]:
    """Пары (подпись, ссылка) для кнопок карточки. Кнопки без ссылки не возвращаются."""
    query = search_query(track)
    q = quote(query, safe="")
    buttons: list[tuple[str, str | None]] = [
        ("Shazam", track.links.get("shazam")),
        ("Apple Music", track.links.get("apple_music")),
        ("Spotify", track.links.get("spotify") or f"https://open.spotify.com/search/{q}"),
        ("YouTube", f"https://www.youtube.com/results?search_query={q}"),
        ("Текст песни", f"https://www.google.com/search?q={quote(query + ' текст', safe='')}"),
    ]
    return [(label, url) for label, url in buttons if url]


def format_history_item(index: int, title: str, artist: str, date: str) -> str:
    return f"{index}. <b>{escape(title)}</b> — {escape(artist)}\n    <i>{escape(date)}</i>"
