"""Tests for the polite fetcher: robots.txt, caching. Uses a fake network."""

import pytest

from gradplanner.scraper.fetch import DisallowedByRobots, PoliteFetcher

ROBOTS = "User-agent: *\nDisallow: /course-search/api/\n"


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.encoding = None

    def raise_for_status(self):
        pass


class FakeSession:
    """Records every URL requested instead of using the internet."""

    def __init__(self):
        self.headers = {}
        self.requested = []

    def get(self, url, timeout):
        self.requested.append(url)
        return FakeResponse(ROBOTS if url.endswith("/robots.txt") else f"<html>{url}</html>")


@pytest.fixture
def fetcher(tmp_path):
    return PoliteFetcher(cache_dir=tmp_path, base_url="https://example.test", delay_seconds=0,
                         session=FakeSession())


def test_second_fetch_uses_cache(fetcher):
    first = fetcher.fetch("page", "/a/")
    second = fetcher.fetch("page", "/a/")
    assert not first.from_cache and second.from_cache
    page_requests = [u for u in fetcher.session.requested if not u.endswith("robots.txt")]
    assert page_requests == ["https://example.test/a/"]


def test_refresh_downloads_again(fetcher):
    fetcher.fetch("page", "/a/")
    fetcher.fetch("page", "/a/", refresh=True)
    assert fetcher.session.requested.count("https://example.test/a/") == 2


def test_robots_disallowed_path_is_refused(fetcher):
    with pytest.raises(DisallowedByRobots):
        fetcher.fetch("api", "/course-search/api/")
    assert "https://example.test/course-search/api/" not in fetcher.session.requested


def test_user_agent_has_no_personal_info(fetcher):
    ua = fetcher.session.headers["User-Agent"]
    assert "@" not in ua
