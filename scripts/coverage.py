"""Coverage report: try a plan for a fictional first-year student in every program.

For each major/minor: how well its requirements were read (full / partial / none),
whether the planner finds an 8-semester plan, whether the independent check
passes, and how long it took. Writes data/coverage_report.md.

Usage:
    python scripts/coverage.py                 # all programs (about 20 minutes)
    python scripts/coverage.py CSC-BS BIO-BS   # just these
"""

import sys
import time
from collections import Counter

from gradplanner import config
from gradplanner.db.store import load_catalog
from gradplanner.planner import plan_for_student, validate_plan
from gradplanner.planner.student import StudentProfile
from gradplanner.planner.terms import Term

REPORT = config.DATA_DIR / "coverage_report.md"


def first_year(program_id: str) -> StudentProfile:
    return StudentProfile(name="Coverage Test (fictional)", program=program_id, entry_term=Term("fall", 2026),
                          next_term=Term("fall", 2026), graduation_term=Term("spring", 2030),
                          language_courses_needed=1)


def main() -> None:
    catalog = load_catalog()
    ids = sys.argv[1:] or sorted(catalog.programs)
    rows = []
    for pid in ids:
        prog = catalog.programs[pid]
        st = first_year(pid)
        start = time.time()
        plan = plan_for_student(catalog, st, time_limit=10)
        secs = time.time() - start
        problems = validate_plan(plan, catalog, st) if plan.ok else []
        result = ("valid" if plan.ok and not problems else "INVALID" if plan.ok else plan.status)
        why = problems[0] if problems else (plan.explanations[0] if plan.explanations else "")
        rows.append((pid, prog.name, prog.school, prog.support, result, secs, why))
        print(f"{pid:22} {prog.support:8} {result:10} {secs:5.1f}s  {why[:90]}", flush=True)

    support = Counter(r[3] for r in rows)
    results = Counter(r[4] for r in rows)
    lines = ["# Program coverage report (fictional first-year student, 8 semesters)", "",
             f"Programs: {len(rows)}. Requirements read: " + ", ".join(f"{k} {v}" for k, v in support.most_common()),
             "Plans: " + ", ".join(f"{k} {v}" for k, v in results.most_common()), "",
             "- **full**: every requirement row was read and is planned automatically.",
             "- **partial**: some rows need an advisor (they show as 'review' in the audit); the rest is planned.",
             "- **none**: requirements are written as paragraphs; only the core curriculum is planned.", "",
             "| Program | Name | School | Read | Plan | Time | Note |", "|---|---|---|---|---|---|---|"]
    for pid, name, school, sup, res, secs, why in rows:
        lines.append(f"| {pid} | {name} | {school} | {sup} | {res} | {secs:.0f}s | {why[:120].replace('|', '/')} |")
    REPORT.write_text("\n".join(lines) + "\n")
    print(f"\n{dict(support)}  {dict(results)}  -> {REPORT}")


if __name__ == "__main__":
    main()
