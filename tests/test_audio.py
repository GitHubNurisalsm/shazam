import subprocess
from pathlib import Path

import pytest

from bot.services import audio
from tests.conftest import make_media, requires_ffmpeg

pytestmark = requires_ffmpeg

# Форматы из раздела 6 ТЗ: (имя файла, аргументы ffmpeg, есть ли видео)
FORMATS = [
    ("voice.ogg", ["-c:a", "libopus"], False),
    ("audio.mp3", ["-c:a", "libmp3lame"], False),
    ("audio.m4a", ["-c:a", "aac"], False),
    ("audio.wav", [], False),
    ("audio.flac", [], False),
    ("video.mp4", ["-c:v", "libx264", "-c:a", "aac", "-shortest"], True),
]


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(out.stdout.strip())


def _channels_and_rate(path: Path) -> tuple[str, str]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=channels,sample_rate",
            "-of",
            "csv=p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rate, channels = out.stdout.strip().split(",")
    return channels, rate


@pytest.mark.parametrize(("name", "args", "video"), FORMATS)
async def test_converts_every_supported_format_to_mono_mp3(
    tmp_path: Path, name: str, args: list[str], video: bool
) -> None:
    src = make_media(tmp_path / name, *args, seconds=6, video=video)

    out = await audio.prepare_fragment(src, tmp_path, fragment_seconds=20)

    assert out.suffix == ".mp3"
    assert _channels_and_rate(out) == ("1", "44100")
    assert _duration(out) == pytest.approx(6, abs=0.5)


async def test_long_file_is_cut_to_fragment_skipping_first_seconds(tmp_path: Path) -> None:
    src = make_media(tmp_path / "long.mp3", seconds=60)

    out = await audio.prepare_fragment(src, tmp_path, fragment_seconds=20)

    assert _duration(out) == pytest.approx(20, abs=0.5)


def test_fragment_start_follows_spec() -> None:
    # Длиннее 30 секунд — фрагмент с 10-й секунды; иначе весь файл с начала.
    assert audio.fragment_start(60, 20) == 10
    assert audio.fragment_start(30, 20) == 0
    assert audio.fragment_start(None, 20) == 0


async def test_rejects_recording_shorter_than_three_seconds(tmp_path: Path) -> None:
    src = make_media(tmp_path / "short.ogg", "-c:a", "libopus", seconds=2)

    with pytest.raises(audio.TooShortError):
        await audio.prepare_fragment(src, tmp_path)


async def test_video_without_audio_track(tmp_path: Path) -> None:
    src = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=5",
            "-c:v",
            "libx264",
            str(src),
        ],
        check=True,
    )
    with pytest.raises(audio.NoAudioStreamError):
        await audio.prepare_fragment(src, tmp_path)


async def test_garbage_file_is_unsupported(tmp_path: Path) -> None:
    src = tmp_path / "input"
    src.write_bytes(b"definitely not a media file" * 100)

    with pytest.raises(audio.UnsupportedFormatError):
        await audio.prepare_fragment(src, tmp_path)


async def test_workspace_is_removed_even_on_error(tmp_path: Path) -> None:
    created: Path | None = None
    with pytest.raises(RuntimeError):
        async with audio.temp_workspace(tmp_path) as workspace:
            created = workspace
            (workspace / "input").write_bytes(b"data")
            raise RuntimeError("boom")
    assert created is not None
    assert not created.exists()
    assert list(tmp_path.iterdir()) == []


async def test_sha256_is_stable(tmp_path: Path) -> None:
    file = tmp_path / "a.bin"
    file.write_bytes(b"abc")
    assert await audio.sha256_file(file) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_cleanup_stale_removes_leftover_workspaces(tmp_path: Path) -> None:
    (tmp_path / "deadbeef").mkdir()
    (tmp_path / "deadbeef" / "input").write_bytes(b"x")
    (tmp_path / ".gitkeep").write_text("")

    audio.cleanup_stale(tmp_path)

    assert [p.name for p in tmp_path.iterdir()] == [".gitkeep"]
