from dataclasses import dataclass
import logging
import httpx
from bot.config import ConfigManager

logger = logging.getLogger("bot.search")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearXNGClient:
    def __init__(self, config_mgr: ConfigManager, http_client: httpx.AsyncClient) -> None:
        self.config_mgr = config_mgr
        self.http = http_client

    async def search(self, query: str) -> list[SearchResult] | None:
        cfg = self.config_mgr.get().search
        url = f"{cfg.searxng_url.rstrip('/')}/search"
        try:
            resp = await self.http.get(
                url,
                params={"q": query, "format": "json"},
                timeout=cfg.timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("SearXNG search failed: %s", type(exc).__name__)
            return None

        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            return []

        results: list[SearchResult] = []
        for item in raw_results[: cfg.max_results]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "Untitled").strip()
            link = str(item.get("url") or "").strip()
            snippet = str(item.get("content") or "").strip()
            if len(snippet) > cfg.max_snippet_chars:
                snippet = snippet[: cfg.max_snippet_chars].rstrip() + "..."
            if link:
                results.append(SearchResult(title=title, url=link, snippet=snippet))

        return results
