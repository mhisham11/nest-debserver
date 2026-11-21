from datetime import datetime
import logging
from zoneinfo import ZoneInfo
from bot.config import ConfigManager
from bot.db import Database

logger = logging.getLogger("bot.budget")


class BudgetTracker:
    def __init__(self, db: Database, config_mgr: ConfigManager) -> None:
        self.db = db
        self.config_mgr = config_mgr
        self._warned_days: set[str] = set()

    def current_day_key(self) -> str:
        tz_name = self.config_mgr.get().budget.timezone
        tz = ZoneInfo(tz_name)
        return datetime.now(tz).strftime("%Y-%m-%d")

    async def is_exceeded(self) -> bool:
        cfg = self.config_mgr.get().budget
        day = self.current_day_key()
        used = await self.db.get_daily_tokens(day)
        exceeded = used >= cfg.daily_token_threshold
        if exceeded and day not in self._warned_days:
            self._warned_days.add(day)
            logger.warning(
                "Daily token threshold reached for %s (%d >= %d). Switching to low_budget models and pausing image generation.",
                day,
                used,
                cfg.daily_token_threshold,
            )
        return exceeded

    async def record_tokens(self, tokens: int, images: int = 0) -> None:
        cfg = self.config_mgr.get().budget
        day = self.current_day_key()
        total = await self.db.add_usage(day, tokens, images)
        if total >= cfg.daily_token_threshold and day not in self._warned_days:
            self._warned_days.add(day)
            logger.warning(
                "Daily token threshold reached for %s (%d >= %d). Switching to low_budget models and pausing image generation.",
                day,
                total,
                cfg.daily_token_threshold,
            )
