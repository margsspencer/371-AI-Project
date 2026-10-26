"""Parse the cached course pages and report anything that needs a human fix.

Usage:
    python scripts/scrape.py          # first, so the pages are cached
    python scripts/validate_data.py   # writes data/staging/*.json and data/validation_report.md
"""

import json
from collections import Counter, defaultdict

from gradplanner import config
from gradplanner.parser.courses import parse_courses_page
from gradplanner.scraper.fetch import PoliteFetcher
from gradplanner.scraper.sources import sources_of_kind
from gradplanner.validation import validate_courses

CATEGORY_ORDER = ["title", "duplicate", "credits", "prerequisite", "corequisite", "permission", "tags", "offering"]


def main() -> None:
    fetcher = PoliteFetcher()
    courses = []
    for source in sources_of_kind("courses"):
        page = fetcher.read_cache(source.key)
        if page is None:
            raise SystemExit(f"Missing cached page '{source.key}'. Run: python scripts/scrape.py")
        courses += parse_courses_page(page.html, source.key)

    config.STAGING_DIR.mkdir(parents=True, exist_ok=True)
    out_json = config.STAGING_DIR / f"courses_{config.BULLETIN_YEAR}.json"
    out_json.write_text(json.dumps(
        {"bulletin_year": config.BULLETIN_YEAR, "courses": [c.to_dict() for c in courses]}, indent=2))

    issues = validate_courses(courses)
    by_category: dict[str, list] = defaultdict(list)
    for issue in issues:
        by_category[issue.category].append(issue)

    # ---- console summary ----
    subjects = Counter(c.subject for c in courses)
    with_prereq = sum(1 for c in courses if c.prereq_raw)
    print(f"Parsed {len(courses)} courses ({', '.join(f'{s}: {n}' for s, n in sorted(subjects.items()))})")
    print(f"  with prerequisite text: {with_prereq}   with corequisite text: "
          f"{sum(1 for c in courses if c.coreq_raw)}")
    print(f"  saved -> {out_json.relative_to(config.PROJECT_ROOT)}")
    print("Issues by category (error = unusable, review = usable but check by hand):")
    for cat in CATEGORY_ORDER:
        items = by_category.get(cat, [])
        if items:
            sev = Counter(i.severity for i in items)
            print(f"  {cat:13s} {len(items):4d}  ({', '.join(f'{k}: {v}' for k, v in sev.items())})")

    # ---- markdown report ----
    lines = [f"# Data validation report: Bulletin {config.BULLETIN_YEAR}", "",
             f"Courses parsed: **{len(courses)}**. Issues: **{len(issues)}**.", ""]
    for cat in CATEGORY_ORDER:
        items = by_category.get(cat, [])
        if not items:
            continue
        lines += [f"## {cat} ({len(items)})", ""]
        if cat == "offering":
            # 100+ identical lines are noise; list the courses compactly.
            assumed = [i.course_id for i in items if i.message.startswith("no offering")]
            other = [i for i in items if not i.message.startswith("no offering")]
            for i in other:
                lines.append(f"- **{i.course_id}** [{i.severity}]: {i.message}")
            lines += ["", f"Assumed fall + spring (NEEDS HUMAN REVIEW), {len(assumed)} courses: "
                      + ", ".join(assumed), ""]
            continue
        for i in items:
            lines.append(f"- **{i.course_id}** [{i.severity}]: {i.message}")
        lines.append("")
    report = config.DATA_DIR / "validation_report.md"
    report.write_text("\n".join(lines))
    print(f"Full report -> {report.relative_to(config.PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
