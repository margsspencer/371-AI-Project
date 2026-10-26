"""Build the SQLite database from the cached Bulletin pages and override files.

Usage:
    python scripts/scrape.py      # download/cache pages (once)
    python scripts/build_db.py    # -> data/bulletin_2026-2027.db and data/validation_report.md
"""

from gradplanner import config
from gradplanner.db.store import load_catalog, save_catalog
from gradplanner.pipeline import build_catalog
from gradplanner.validation import report_markdown, summary_lines, validate_catalog


def main() -> None:
    catalog, problems = build_catalog()
    issues = validate_catalog(catalog, problems)
    path = save_catalog(catalog)
    # Read it straight back as a sanity check that save and load agree.
    reloaded = load_catalog(path)
    assert reloaded.courses.keys() == catalog.courses.keys(), "database round-trip lost courses"
    for line in summary_lines(catalog, issues):
        print(line)
    errors = [i for i in issues if i.severity == "error"]
    for i in errors:
        print(f"  ERROR {i.subject_id}: {i.message}")
    report = config.DATA_DIR / "validation_report.md"
    report.write_text(report_markdown(catalog, issues))
    print(f"Database -> {path.relative_to(config.PROJECT_ROOT)}")
    print(f"Report   -> {report.relative_to(config.PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
