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
        headers = {
            "Accept": "application/json",
            "X-Forwarded-For": "127.0.0.1",
            "X-Real-IP": "127.0.0.1",
            "User-Agent": "Mozilla/5.0 (compatible; AIDiscordBot/1.0)",
        }
        try:
            resp = await self.http.get(
                url,
                params={"q": query, "format": "json"},
                headers=headers,
                timeout=cfg.timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "SearXNG search failed with HTTP %d for query=%r",
                exc.response.status_code,
                query,
            )
            return None
        except Exception as exc:
            logger.error("SearXNG search failed for query=%r: %s", query, type(exc).__name__)
            return None

        if not isinstance(data, dict):
            logger.error("SearXNG returned non-dict JSON for query=%r", query)
            return None

        results: list[SearchResult] = []

        raw_answers = data.get("answers")
        if isinstance(raw_answers, list):
            for ans in raw_answers:
                ans_text = ""
                ans_url = ""
                if isinstance(ans, str):
                    ans_text = ans.strip()
                elif isinstance(ans, dict):
                    ans_text = str(ans.get("answer") or ans.get("content") or "").strip()
                    ans_url = str(ans.get("url") or "").strip()
                if ans_text:
                    if len(ans_text) > cfg.max_snippet_chars:
                        ans_text = ans_text[: cfg.max_snippet_chars].rstrip() + "..."
                    results.append(
                        SearchResult(
                            title="Instant Answer",
                            url=ans_url or "https://searxng.local/answer",
                            snippet=ans_text,
                        )
                    )

        raw_infoboxes = data.get("infoboxes")
        if isinstance(raw_infoboxes, list):
            for box in raw_infoboxes:
                if not isinstance(box, dict):
                    continue
                title = str(box.get("infobox") or box.get("title") or "Infobox").strip()
                snippet = str(box.get("content") or "").strip()
                box_url = ""
                urls = box.get("urls")
                if isinstance(urls, list) and urls and isinstance(urls[0], dict):
                    box_url = str(urls[0].get("url") or "").strip()
                if snippet:
                    if len(snippet) > cfg.max_snippet_chars:
                        snippet = snippet[: cfg.max_snippet_chars].rstrip() + "..."
                    results.append(
                        SearchResult(
                            title=title,
                            url=box_url or "https://searxng.local/infobox",
                            snippet=snippet,
                        )
                    )

        raw_results = data.get("results")
        if isinstance(raw_results, list):
            for item in raw_results:
                if len(results) >= cfg.max_results:
                    break
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "Untitled").strip()
                link = str(item.get("url") or "").strip()
                snippet = str(item.get("content") or "").strip()
                if len(snippet) > cfg.max_snippet_chars:
                    snippet = snippet[: cfg.max_snippet_chars].rstrip() + "..."
                if link:
                    results.append(SearchResult(title=title, url=link, snippet=snippet))

        if not results:
            unresponsive = data.get("unresponsive_engines")
            logger.warning(
                "SearXNG returned 0 results for query=%r (unresponsive_engines=%s)",
                query,
                unresponsive,
            )

        return results[: cfg.max_results]