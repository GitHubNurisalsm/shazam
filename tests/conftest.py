import json
import shutil
import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.db.models import Base

SAMPLES = Path(__file__).parent / "samples"

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


def load_sample(name: str) -> Any:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def make_media(path: Path, *args: str, seconds: float = 5, video: bool = False) -> Path:
    """Генерирует тестовое медиа через ffmpeg: синус 440 Гц, опционально с видео."""
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y"]
    if video:
        cmd += ["-f", "lavfi", "-i", f"color=c=black:s=64x64:d={seconds}"]
    cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", *args, str(path)]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db_session:
        yield db_session
    await engine.dispose()
