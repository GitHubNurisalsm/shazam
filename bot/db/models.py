from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Время в UTC без tzinfo: SQLite не хранит часовой пояс, храним единообразно."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(128))
    language_code: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class Recognition(Base):
    __tablename__ = "recognitions"
    __table_args__ = (Index("ix_recognitions_user_id_created_at", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    media_type: Mapped[str] = mapped_column(String(16))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column(Boolean)
    provider: Mapped[str | None] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(String(256))
    artist: Mapped[str | None] = mapped_column(String(256))
    shazam_key: Mapped[str | None] = mapped_column(String(32))
    cover_url: Mapped[str | None] = mapped_column(Text)
    links_json: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
