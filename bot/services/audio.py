"""Подготовка медиа к распознаванию: ffprobe/ffmpeg, обрезка, хеш, временные файлы.

Модуль не знает про Telegram: на вход — путь к скачанному файлу, на выходе — MP3-фрагмент.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import shutil
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT = 15.0
FFPROBE_TIMEOUT = 10.0
MIN_DURATION = 3.0
# Если запись длиннее SKIP_START + fragment, начало пропускаем: оно часто тихое.
SKIP_START = 10.0


class AudioError(Exception):
    """Базовая ошибка подготовки аудио."""


class UnsupportedFormatError(AudioError):
    """Файл не является медиа, которое понимает ffmpeg."""


class NoAudioStreamError(AudioError):
    """В файле нет звуковой дорожки."""


class TooShortError(AudioError):
    """Запись короче MIN_DURATION секунд."""


class ConversionError(AudioError):
    """ffmpeg завершился с ошибкой или по таймауту."""


@dataclass(frozen=True)
class MediaProbe:
    duration: float | None
    has_audio: bool


def check_ffmpeg() -> list[str]:
    """Возвращает список отсутствующих в PATH утилит."""
    return [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]


async def _run(args: list[str], time_limit: float) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=time_limit)
    except BaseException:
        # Таймаут или отмена задачи: процесс не должен остаться висеть.
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        raise
    return process.returncode or 0, stdout, stderr


async def probe(path: Path) -> MediaProbe:
    try:
        code, stdout, stderr = await _run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            FFPROBE_TIMEOUT,
        )
    except TimeoutError as exc:
        raise UnsupportedFormatError("ffprobe timeout") from exc
    if code != 0:
        raise UnsupportedFormatError(stderr.decode(errors="replace")[-200:])
    try:
        data = json.loads(stdout)
        streams = data.get("streams") or []
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        raw_duration = (data.get("format") or {}).get("duration")
        duration = float(raw_duration) if raw_duration not in (None, "N/A") else None
    except (ValueError, TypeError, AttributeError) as exc:
        raise UnsupportedFormatError("unexpected ffprobe output") from exc
    return MediaProbe(duration=duration, has_audio=has_audio)


def fragment_start(duration: float | None, fragment_seconds: int) -> float:
    if duration is not None and duration > SKIP_START + fragment_seconds:
        return SKIP_START
    return 0.0


async def prepare_fragment(src: Path, dst_dir: Path, fragment_seconds: int = 20) -> Path:
    """Превращает любое медиа в MP3 моно 44,1 кГц длиной не больше fragment_seconds."""
    info = await probe(src)
    if not info.has_audio:
        raise NoAudioStreamError
    if info.duration is not None and info.duration < MIN_DURATION:
        raise TooShortError

    start = fragment_start(info.duration, fragment_seconds)
    out = dst_dir / "fragment.mp3"
    try:
        code, _, stderr = await _run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{start:.2f}",
                "-t",
                str(fragment_seconds),
                "-i",
                str(src),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "44100",
                "-b:a",
                "128k",
                str(out),
            ],
            FFMPEG_TIMEOUT,
        )
    except TimeoutError as exc:
        raise ConversionError("ffmpeg timeout") from exc
    if code != 0 or not out.exists():
        raise ConversionError(stderr.decode(errors="replace")[-200:])

    # Длительность в контейнере бывает неизвестна или неверна — проверяем результат.
    result = await probe(out)
    if result.duration is None or result.duration < MIN_DURATION:
        raise TooShortError
    return out


def _sha256_sync(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def sha256_file(path: Path) -> str:
    return await asyncio.to_thread(_sha256_sync, path)


@contextlib.asynccontextmanager
async def temp_workspace(base: Path) -> AsyncGenerator[Path, None]:
    """Создаёт tmp/<uuid> и удаляет его после обработки, даже при ошибке."""
    path = base / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        await asyncio.to_thread(shutil.rmtree, path, True)


def cleanup_stale(base: Path) -> None:
    """Удаляет хвосты, оставшиеся после аварийного завершения процесса."""
    if not base.exists():
        return
    for child in base.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
