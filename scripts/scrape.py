"""Download (or reuse cached copies of) every Bulletin page we need.

Usage:
    python scripts/scrape.py            # uses the cache; only downloads missing pages
    python scripts/scrape.py --refresh  # re-download everything (e.g. new Bulletin year)
    python scripts/scrape.py --all      # also find and download every department, program, and subject
"""

import argparse

from gradplanner import config
from gradplanner.scraper.discover import discover
from gradplanner.scraper.fetch import DisallowedByRobots, PoliteFetcher
from gradplanner.scraper.sources import SOURCES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="ignore the cache and re-download")
    parser.add_argument("--all", action="store_true", help="discover and fetch all departments and programs")
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

    if args.all:
        pages = discover(fetcher)
        print(f"Discovered {sum(p.kind == 'department' for p in pages)} departments, "
              f"{sum(p.kind == 'program' for p in pages)} program pages, "
              f"{sum(p.kind == 'courses' for p in pages)} course subjects")
        for p in pages:
            if p.kind == "department":
                continue
            try:
                page = fetcher.fetch(p.key, p.path, refresh=args.refresh)
            except Exception as exc:
                print(f"  FAIL  {p.key}: {exc}")
                continue
            if not page.from_cache:
                print(f"  fetched {p.key:40s} {len(page.html):>7,d} chars")


if __name__ == "__main__":
    main()
