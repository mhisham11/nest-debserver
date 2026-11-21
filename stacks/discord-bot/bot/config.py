import json
import logging
import os
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("bot.config")


class HackClubConfig(BaseModel):
    base_url: str
    request_timeout_seconds: float = Field(gt=0)
    image_endpoint: str = "/images/generations"


class ModelsConfig(BaseModel):
    mention: list[str] = Field(min_length=1)
    chat: list[str] = Field(min_length=1)
    vision: list[str] = Field(default_factory=list)
    low_budget: list[str] = Field(min_length=1)
    image: list[str] = Field(min_length=1)


class ServerChatModeOverride(BaseModel):
    chance: float | None = Field(default=None, ge=0.0, le=1.0)
    channel_cooldown_seconds: float | None = Field(default=None, ge=0.0)


class ChatModeConfig(BaseModel):
    chance: float = Field(ge=0.0, le=1.0)
    channel_cooldown_seconds: float = Field(ge=0.0)
    servers: dict[str, ServerChatModeOverride] = Field(default_factory=dict)

    def get_for_guild(self, guild_id: int) -> tuple[float, float]:
        override = self.servers.get(str(guild_id))
        if override is None:
            return self.chance, self.channel_cooldown_seconds
        chance = override.chance if override.chance is not None else self.chance
        cooldown = (
            override.channel_cooldown_seconds
            if override.channel_cooldown_seconds is not None
            else self.channel_cooldown_seconds
        )
        return chance, cooldown


class ContextConfig(BaseModel):
    messages: int = Field(ge=0, le=100)
    max_chars_per_message: int = Field(ge=50, le=10000)


class BudgetConfig(BaseModel):
    daily_token_threshold: int = Field(ge=0)
    timezone: str = "UTC"
    image_token_cost: int = Field(ge=0)

    @field_validator("timezone")
    @classmethod
    def validate_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Invalid timezone: {v}") from exc
        return v


class TriggersConfig(BaseModel):
    search: list[str] = Field(min_length=1)
    image: list[str] = Field(min_length=1)


class SearchConfig(BaseModel):
    searxng_url: str
    max_results: int = Field(ge=1, le=20)
    timeout_seconds: float = Field(gt=0)
    max_snippet_chars: int = Field(ge=50, le=5000)


class LimitsConfig(BaseModel):
    max_output_tokens: int = Field(ge=1)
    max_images_per_message: int = Field(ge=1, le=10)
    max_image_bytes: int = Field(ge=1024)
    user_cooldown_seconds: float = Field(ge=0.0)
    max_instruction_chars: int = Field(ge=100)
    max_concurrent_requests: int = Field(default=4, ge=1, le=50)


class AppConfig(BaseModel):
    default_mode: Literal["off", "mention", "chat"] = "mention"
    hackclub: HackClubConfig
    models: ModelsConfig
    chat_mode: ChatModeConfig
    context: ContextConfig
    budget: BudgetConfig
    triggers: TriggersConfig
    search: SearchConfig
    limits: LimitsConfig


class ConfigManager:
    def __init__(self, path: str = "/app/config.json") -> None:
        self.path = Path(path)
        if not self.path.exists():
            # Fallback to local relative path if running outside container
            self.path = Path("config.json")
        self._mtime: float = 0.0
        self._config: AppConfig | None = None
        self.load_initial()

    def load_initial(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"Configuration file not found at {self.path}")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self._config = AppConfig.model_validate(raw)
        self._mtime = os.path.getmtime(self.path)
        logger.info("Configuration loaded and validated successfully.")

    def get(self) -> AppConfig:
        try:
            current_mtime = os.path.getmtime(self.path)
            if current_mtime > self._mtime:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                new_cfg = AppConfig.model_validate(raw)
                self._config = new_cfg
                self._mtime = current_mtime
                logger.info("Hot-reloaded config.json successfully.")
        except Exception as exc:
            logger.error("Failed to hot-reload config.json, keeping previous valid config: %s", exc)
            try:
                self._mtime = os.path.getmtime(self.path)
            except OSError:
                pass
        assert self._config is not None
        return self._config