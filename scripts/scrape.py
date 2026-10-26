"""Download (or reuse cached copies of) every Bulletin page we need.

Usage:
    python scripts/scrape.py            # uses the cache; only downloads missing pages
    python scripts/scrape.py --refresh  # re-download everything (e.g. new Bulletin year)
"""

import argparse

from gradplanner import config
from gradplanner.scraper.fetch import DisallowedByRobots, PoliteFetcher
from gradplanner.scraper.sources import SOURCES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="ignore the cache and re-download")
    args = parser.parse_args()

    fetcher = PoliteFetcher()
    print(f"Bulletin {config.BULLETIN_YEAR} -> cache folder {config.RAW_DIR}")
    for source in SOURCES:
        try:
            page = fetcher.fetch(source.key, source.path, refresh=args.refresh)
        except DisallowedByRobots as exc:
            print(f"  SKIP  {source.key:24s} disallowed by robots.txt: {exc}")
            continue
        status = "cached " if page.from_cache else "fetched"
        print(f"  {status} {source.key:24s} {len(page.html):>7,d} chars  {page.url}")


if __name__ == "__main__":
    main()
