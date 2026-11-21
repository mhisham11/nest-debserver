import logging
import os
import re
import shutil
from pathlib import Path
from bot.config import ConfigManager

logger = logging.getLogger("bot.instructions")

PLACEHOLDER_HEADER = """<!--
Per-server personality instructions for this Discord server.
Any non-empty text outside HTML comments in this file will replace default.md for this server.
Leave this file empty (or with only this comment) to use default.md.
Changes take effect automatically without restarting the bot.
-->
"""

COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def slugify_server_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "server"


class InstructionManager:
    def __init__(self, config_mgr: ConfigManager, base_dir: str = "/app/data/instructions") -> None:
        self.config_mgr = config_mgr
        self.base_dir = Path(base_dir)
        if not self.base_dir.parent.exists():
            self.base_dir = Path("data/instructions")
        self.servers_dir = self.base_dir / "servers"
        self.default_file = self.base_dir / "default.md"
        self._cache: dict[Path, tuple[float, str]] = {}
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        self.servers_dir.mkdir(parents=True, exist_ok=True)
        if not self.default_file.exists():
            bundled = Path("/app/default_instructions.md")
            if bundled.exists():
                shutil.copyfile(bundled, self.default_file)
            else:
                self.default_file.write_text(
                    "You are a friendly, concise, and helpful community Discord bot.\n",
                    encoding="utf-8",
                )

    def _find_guild_file(self, guild_id: int) -> Path | None:
        prefix = f"{guild_id}__"
        for entry in self.servers_dir.glob(f"{prefix}*.md"):
            if entry.is_file():
                return entry
        return None

    def ensure_guild_file(self, guild_id: int, guild_name: str) -> Path:
        slug = slugify_server_name(guild_name)
        target_name = f"{guild_id}__{slug}.md"
        target_path = self.servers_dir / target_name

        existing = self._find_guild_file(guild_id)
        if existing is None:
            target_path.write_text(PLACEHOLDER_HEADER, encoding="utf-8")
            logger.info("Created instruction file for guild %d", guild_id)
            return target_path

        if existing != target_path:
            existing.rename(target_path)
            self._cache.pop(existing, None)
            logger.info("Renamed instruction file for guild %d to %s", guild_id, target_name)

        return target_path

    def _read_cached(self, path: Path) -> str:
        if not path.exists():
            return ""
        mtime = os.path.getmtime(path)
        cached = self._cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]

        raw = path.read_text(encoding="utf-8")
        cleaned = COMMENT_RE.sub("", raw).strip()
        self._cache[path] = (mtime, cleaned)
        return cleaned

    def get_instructions(self, guild_id: int, guild_name: str) -> str:
        max_chars = self.config_mgr.get().limits.max_instruction_chars
        guild_file = self.ensure_guild_file(guild_id, guild_name)
        content = self._read_cached(guild_file)
        source_label = f"guild:{guild_id}"

        if not content:
            content = self._read_cached(self.default_file)
            source_label = "default.md"

        if len(content) > max_chars:
            logger.warning(
                "Instruction file (%s) exceeds %d chars (%d chars); truncating.",
                source_label,
                max_chars,
                len(content),
            )
            content = content[:max_chars]

        return content
