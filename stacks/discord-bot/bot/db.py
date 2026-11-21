from datetime import datetime, timezone
from pathlib import Path
import aiosqlite

DB_PATH = Path("/app/data/bot.db")
if not DB_PATH.parent.exists():
    DB_PATH = Path("data/bot.db")


class Database:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL,
                updated_at TEXT
            )
            """
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_usage (
                day TEXT PRIMARY KEY,
                tokens INTEGER NOT NULL,
                images INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def get_guild_mode(self, guild_id: int, default_mode: str) -> str:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT mode FROM guild_settings WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return str(row[0])
        await self.set_guild_mode(guild_id, default_mode)
        return default_mode

    async def set_guild_mode(self, guild_id: int, mode: str) -> None:
        assert self._conn is not None
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """
            INSERT INTO guild_settings (guild_id, mode, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                mode = excluded.mode,
                updated_at = excluded.updated_at
            """,
            (guild_id, mode, now),
        )
        await self._conn.commit()

    async def get_daily_tokens(self, day: str) -> int:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT tokens FROM daily_usage WHERE day = ?", (day,)
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def add_usage(self, day: str, tokens: int, images: int = 0) -> int:
        assert self._conn is not None
        await self._conn.execute(
            """
            INSERT INTO daily_usage (day, tokens, images)
            VALUES (?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                tokens = daily_usage.tokens + excluded.tokens,
                images = daily_usage.images + excluded.images
            """,
            (day, max(0, tokens), max(0, images)),
        )
        await self._conn.commit()
        return await self.get_daily_tokens(day)
