# SongFinder Bot

Telegram-бот, который по фрагменту аудио или видео определяет название песни и исполнителя.
Распознавание выполняет [ShazamIO](https://pypi.org/project/shazamio/), резервный провайдер —
[AudD](https://audd.io/).

Принимает голосовые, аудиофайлы (MP3, M4A, OGG, WAV, FLAC), видео, «кружки» и документы
с аудио/видео, в том числе пересланные. Отвечает карточкой трека: обложка, название, исполнитель,
альбом, год, жанр и кнопки Shazam / Apple Music / Spotify / YouTube / «Текст песни».

## Быстрый старт (Docker)

1. Создайте бота у [@BotFather](https://t.me/BotFather) и получите токен.
2. Узнайте свой Telegram ID (например, у [@userinfobot](https://t.me/userinfobot)).
3. Склонируйте репозиторий и создайте `.env`:

   ```bash
   cp .env.example .env
   # впишите BOT_TOKEN и ADMIN_IDS
   ```

4. Запустите:

   ```bash
   docker compose up -d --build
   docker compose logs -f
   ```

Миграции (`alembic upgrade head`) выполняются автоматически перед стартом. База лежит в `./data`,
логи — в `./logs`. Контейнер перезапускается сам (`restart: unless-stopped`).

## Локальный запуск

Нужны Python 3.11+ и ffmpeg в `PATH` (`brew install ffmpeg`, `apt install ffmpeg`,
на Windows — [ffmpeg.org](https://ffmpeg.org/download.html)).

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env             # впишите BOT_TOKEN и ADMIN_IDS
alembic upgrade head
python -m bot
```

## Настройки (`.env`)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BOT_TOKEN` | — | Токен от @BotFather, обязательно |
| `ADMIN_IDS` | пусто | ID администраторов через запятую (доступ к `/stats`, алерты) |
| `DATABASE_URL` | `sqlite+aiosqlite:///data/bot.db` | Для PostgreSQL: `postgresql+asyncpg://...` (нужен пакет `asyncpg`) |
| `AUDD_TOKEN` | пусто | Токен AudD; пусто — резервный провайдер выключен |
| `RATE_LIMIT_PER_MINUTE` | 5 | Запросов на распознавание в минуту на пользователя |
| `DAILY_LIMIT` | 50 | Распознаваний за последние 24 часа на пользователя |
| `MAX_FILE_MB` | 20 | Максимальный размер файла (лимит Bot API — 20 МБ) |
| `FRAGMENT_SECONDS` | 20 | Длина фрагмента, который уходит провайдеру |
| `LOG_LEVEL` | INFO | Уровень логирования |

## Команды

- `/start` — приветствие и регистрация;
- `/help` — форматы, советы по записи, лимиты;
- `/history` — найденные треки, по 10 на страницу;
- `/stats` — статистика за сутки, неделю и всё время, топ-10 (только для `ADMIN_IDS`).

Заблокировать пользователя можно вручную:
`sqlite3 data/bot.db "UPDATE users SET is_banned = 1 WHERE id = <user_id>"`.

## Как это устроено

```
Telegram → DbSessionMiddleware → UserMiddleware (регистрация, бан)
        → ThrottlingMiddleware (N/мин) → handlers/media.py
              ├─ очередь пользователя + суточный лимит (по таблице recognitions)
              ├─ services/audio.py: скачивание в tmp/<uuid>, SHA-256, ffmpeg → MP3 моно 44,1 кГц ≤ 20 с
              ├─ db/repo.py: кэш по хешу файла (24 ч)
              ├─ services/recognition/manager.py: ShazamIO → повтор → AudD
              └─ services/formatter.py + keyboards/track.py: карточка
```

Обработчики Telegram не знают, какой провайдер распознаёт музыку, а сервисы не знают про Telegram.
Новый провайдер — это класс с методом `async recognize(path) -> TrackResult | None`.

Ограничения: счётчики частоты и очередь пользователя хранятся в памяти процесса, поэтому бот
должен работать в одном экземпляре.

## Разработка

```bash
pytest --cov            # тесты + покрытие services/
ruff check . && ruff format --check .
mypy
alembic check           # нет неучтённых изменений схемы
```

Тесты не ходят в сеть: ответы провайдеров лежат в `tests/samples/*.json`, аудио генерируется
через ffmpeg, Telegram подменён фейковой сессией (`tests/test_bot_flow.py`).

Изменения схемы — только через миграции:

```bash
alembic revision --autogenerate -m "описание"
```
