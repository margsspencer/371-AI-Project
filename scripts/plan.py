"""Make a semester plan for a (fictional) student profile and check it.

Usage:
    python scripts/plan.py samples/sam_first_year_bs.yaml
    python scripts/plan.py samples/sam_first_year_bs.yaml --time 60
"""

import argparse

from gradplanner.db.store import load_catalog
from gradplanner.planner import load_profile, plan_for_student, validate_plan
from gradplanner.recommender import recommend_for_student


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile")
    ap.add_argument("--time", type=float, default=10, help="solver time limit in seconds")
    args = ap.parse_args()

    catalog = load_catalog()
    student = load_profile(args.profile)
    recs, relevance = recommend_for_student(catalog, student)
    plan = plan_for_student(catalog, student, time_limit=args.time, relevance=relevance)

    status = {"optimal": "best plan", "feasible": "good plan (time limit reached before proving it's the best)"}
    print(f"Plan for {plan.student_name}: {plan.program_id}  [{status.get(plan.status, plan.status)}]")
    print("Advisory only: confirm every plan with your academic advisor and the current Bulletin.\n")
    for reason in plan.explanations:
        print("NO PLAN:", reason)
    for tp in plan.terms:
        print(f"{tp.term}  ({tp.hours:g} h)")
        for item in tp.items:
            toward = ", ".join(plan.labels.get(k, k) for k in item.counts_toward)
            print(f"   {item.id:<10} {item.hours:>4g} h  {item.title[:42]:<42} {toward}")
    if recs:
        print("\nCourses that match your career goal (0-100):")
        for r in recs:
            where = plan.term_of(r.course_id)
            print(f"   {r.course_id:<8} {r.score:>3}  {r.title[:40]:<40} "
                  + (f"in plan: {where}" if where else "not in plan"))
    for note in plan.notes:
        print("note:", note)
    if plan.ok:
        problems = validate_plan(plan, catalog, student)
        print("\nIndependent check:", "VALID" if not problems else f"{len(problems)} problem(s)")
        for p in problems:
            print("  -", p)


if __name__ == "__main__":
    main()
