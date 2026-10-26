"""Parse the cached Bulletin pages and report anything that needs a human fix.

Usage:
    python scripts/scrape.py          # first, so the pages are cached
    python scripts/validate_data.py   # writes data/validation_report.md (does not touch the database)
"""

from gradplanner import config
from gradplanner.pipeline import build_catalog
from gradplanner.validation import report_markdown, summary_lines, validate_catalog


def main() -> None:
    catalog, problems = build_catalog()
    issues = validate_catalog(catalog, problems)
    for line in summary_lines(catalog, issues):
        print(line)
    for i in issues:
        if i.severity == "error":
            print(f"  ERROR {i.subject_id}: {i.message}")
    report = config.DATA_DIR / "validation_report.md"
    report.write_text(report_markdown(catalog, issues))
    print(f"Full report -> {report.relative_to(config.PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
