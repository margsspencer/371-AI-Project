"""Polite downloading of Bulletin pages, with a local cache.

Three rules keep us polite:
1. We check robots.txt before every new URL and refuse disallowed paths.
2. We wait at least REQUEST_DELAY_SECONDS between requests.
3. We save every page to disk and reuse it, so a page is downloaded once
   per Bulletin year unless someone passes refresh=True.
"""

from __future__ import annotations

import json
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from gradplanner import config


class DisallowedByRobots(Exception):
    """Raised when robots.txt says we may not fetch a URL."""


@dataclass
class CachedPage:
    url: str
    html: str
    fetched_at: str
    from_cache: bool


class PoliteFetcher:
    """Fetches pages from one site, respecting robots.txt, a delay, and a cache."""

    def __init__(
        self,
        cache_dir: Path = config.RAW_DIR,
        base_url: str = config.BULLETIN_BASE_URL,
        delay_seconds: float = config.REQUEST_DELAY_SECONDS,
        session: requests.Session | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.base_url = base_url.rstrip("/")
        self.delay_seconds = delay_seconds
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT
        self._last_request_time = 0.0
        self._robots: urllib.robotparser.RobotFileParser | None = None

    # --- cache helpers -------------------------------------------------
    def _cache_paths(self, key: str) -> tuple[Path, Path]:
        return self.cache_dir / f"{key}.html", self.cache_dir / f"{key}.meta.json"

    def read_cache(self, key: str) -> CachedPage | None:
        """Return the cached page for `key`, or None if we haven't saved it yet."""
        html_path, meta_path = self._cache_paths(key)
        if not (html_path.exists() and meta_path.exists()):
            return None
        meta = json.loads(meta_path.read_text())
        return CachedPage(meta["url"], html_path.read_text(encoding="utf-8"), meta["fetched_at"], True)

    def _write_cache(self, key: str, page: CachedPage) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        html_path, meta_path = self._cache_paths(key)
        html_path.write_text(page.html, encoding="utf-8")
        meta = {"url": page.url, "fetched_at": page.fetched_at, "bulletin_year": config.BULLETIN_YEAR}
        meta_path.write_text(json.dumps(meta, indent=2))

    # --- network helpers -----------------------------------------------
    def _wait_for_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        self._last_request_time = time.monotonic()

    def _robots_parser(self) -> urllib.robotparser.RobotFileParser:
        if self._robots is None:
            self._wait_for_rate_limit()
            resp = self.session.get(f"{self.base_url}/robots.txt", timeout=30)
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(resp.text.splitlines())
            self._robots = parser
        return self._robots

    def is_allowed(self, url: str) -> bool:
        """True if robots.txt allows our User-Agent to fetch `url`."""
        return self._robots_parser().can_fetch(config.USER_AGENT, url)

    def fetch(self, key: str, path: str, refresh: bool = False) -> CachedPage:
        """Return the page at `path`, using the cache unless `refresh` is True."""
        if not refresh:
            cached = self.read_cache(key)
            if cached is not None:
                return cached

        url = self.base_url + path
        if not self.is_allowed(url):
            raise DisallowedByRobots(url)

        self._wait_for_rate_limit()
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        page = CachedPage(url, resp.text, datetime.now(timezone.utc).isoformat(timespec="seconds"), False)
        self._write_cache(key, page)
        return page
