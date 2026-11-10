"""Export a (fictional) student's plan as a spreadsheet (CSV) and a calendar file (.ics).

Usage:
    python scripts/export.py samples/sam_first_year_bs.yaml                 # writes exports/<name>.csv/.ics
    python scripts/export.py samples/casey_double_major.yaml --out my_folder

Open the .csv in Excel/Sheets; import the .ics into Google Calendar, Apple Calendar,
or Outlook. Dates after 2026-27 are projected from the Bulletin calendar ("estimated").
"""

import argparse
import re
from pathlib import Path

from gradplanner.db.store import load_catalog
from gradplanner.export import plan_to_csv, plan_to_ics
from gradplanner.planner import load_profile, plan_for_student, validate_plan
from gradplanner.recommender import recommend_for_student


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile")
    ap.add_argument("--out", default="exports", help="folder for the files (default: exports/)")
    ap.add_argument("--time", type=float, default=10)
    args = ap.parse_args()

    catalog = load_catalog()
    student = load_profile(args.profile)
    recs, relevance = recommend_for_student(catalog, student)
    plan = plan_for_student(catalog, student, time_limit=args.time, relevance=relevance)
    if not plan.ok:
        raise SystemExit("No plan: " + " ".join(plan.explanations))
    problems = validate_plan(plan, catalog, student)
    if problems:
        raise SystemExit("The plan didn't pass the independent check:\n  " + "\n  ".join(problems))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-z0-9]+", "_", student.name.lower()).strip("_")
    (out / f"{stem}.csv").write_text(plan_to_csv(plan, {r.course_id for r in recs}))
    (out / f"{stem}.ics").write_bytes(plan_to_ics(plan, catalog))
    print(f"Wrote {out / (stem + '.csv')} and {out / (stem + '.ics')}")


if __name__ == "__main__":
    main()
