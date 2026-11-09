"""Finish the first planned semester, mark how it went, and re-plan (fictional students only).

Usage:
    python scripts/replan.py samples/sam_first_year_bs.yaml --failed "CSC 111"
    python scripts/replan.py samples/sam_first_year_bs.yaml --dropped "MTH 111" --save scratch/sam_after.yaml

Every course in the first semester not listed as failed or dropped counts as passed.
The new plan keeps the old one's choices where it can; the output lists what changed.
"""

import argparse

from gradplanner.db.store import load_catalog
from gradplanner.planner import load_profile, plan_for_student, validate_plan
from gradplanner.planner.replan import diff_plans, record_term
from gradplanner.planner.student import save_profile
from gradplanner.recommender import recommend_for_student


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile")
    ap.add_argument("--failed", action="append", default=[], help="course id (repeatable)")
    ap.add_argument("--dropped", action="append", default=[], help="course id (repeatable)")
    ap.add_argument("--save", help="write the updated profile (YAML) here")
    ap.add_argument("--time", type=float, default=10)
    args = ap.parse_args()

    catalog = load_catalog()
    student = load_profile(args.profile)
    _, relevance = recommend_for_student(catalog, student)
    old = plan_for_student(catalog, student, time_limit=args.time, relevance=relevance)
    if not old.ok:
        raise SystemExit("No starting plan: " + " ".join(old.explanations))
    first = old.terms[0]
    outcomes = {c: "failed" for c in args.failed} | {c: "dropped" for c in args.dropped}
    unknown = [c for c in outcomes if c not in first.course_ids]
    if unknown:
        raise SystemExit(f"Not planned in {first.term}: {', '.join(unknown)}. Planned: {', '.join(first.course_ids)}")

    after = record_term(student, old, outcomes)
    new = plan_for_student(catalog, after, time_limit=args.time, relevance=relevance, previous=old)
    print(f"{student.name}: {first.term} recorded ({', '.join(f'{c} {r}' for c, r in outcomes.items()) or 'all passed'}).")
    print("Advisory only: confirm with your academic advisor.\n")
    if not new.ok:
        print("NO NEW PLAN:", *new.explanations, sep="\n  ")
        return
    diff = diff_plans(old, new, outcomes, recorded=first.term)
    print("What changed:", diff.summary)
    for c in diff.changes:
        print("  -", c.sentence)
    problems = validate_plan(new, catalog, after)
    print("\nIndependent check of the new plan:", "VALID" if not problems else f"{len(problems)} problem(s)")
    for p in problems:
        print("  -", p)
    if args.save:
        save_profile(after, args.save)
        print(f"\nUpdated profile saved to {args.save}")


if __name__ == "__main__":
    main()
