"""Degree audit for a (fictional) student profile.

Usage:
    python scripts/audit.py samples/jordan_junior_bs.yaml           # completed + in-progress courses only
    python scripts/audit.py samples/jordan_junior_bs.yaml --plan    # also make a plan and count it
"""

import argparse

from gradplanner.audit import run_audit
from gradplanner.db.store import load_catalog
from gradplanner.planner import load_profile, plan_for_student

MARK = {"done": "[x]", "in_progress": "[~]", "planned": "[p]", "remaining": "[ ]", "review": "[?]"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile")
    ap.add_argument("--plan", action="store_true", help="make a plan first and include it")
    args = ap.parse_args()

    catalog = load_catalog()
    student = load_profile(args.profile)
    plan = None
    if args.plan:
        plan = plan_for_student(catalog, student)
        if not plan.ok:
            print("No plan:", *plan.explanations, sep="\n  ")
            plan = None
    audit = run_audit(catalog, student, plan)

    print(f"Degree audit for {audit.student_name}: {audit.program_id}"
          + ("  (including the plan)" if plan else ""))
    print("Advisory only: confirm with your academic advisor and the current Bulletin.")
    print("[x] done  [~] in progress  [p] planned  [ ] remaining  [?] advisor review\n")
    section = None
    for line in audit.lines:
        if line.section != section:
            section = line.section
            print(section)
        print(f"  {MARK[line.status]} {line.label[:58]:<58} {line.detail}")
    print("\n" + ", ".join(f"{audit.count(s)} {s.replace('_', ' ')}" for s in MARK))
    for note in audit.notes:
        print("note:", note)


if __name__ == "__main__":
    main()
