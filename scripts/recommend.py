"""Recommend courses for a career goal.

Usage:
    python scripts/recommend.py --list                                  # career pick-list
    python scripts/recommend.py --career machine-learning-engineer
    python scripts/recommend.py --text "robotics and computer vision"
    python scripts/recommend.py --career data-scientist --program CSC-BA --top 8
"""

import argparse
import textwrap

from gradplanner.db.store import load_catalog
from gradplanner.recommender import Recommender, career_query, load_careers
from gradplanner.recommender.tfidf import WEAK_SCORE


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--career", help="pick-list id (see --list)")
    ap.add_argument("--text", default="", help="career goal in your own words")
    ap.add_argument("--program", default="CSC-BS")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    careers = load_careers()
    if args.list or not (args.career or args.text):
        for c in careers.values():
            print(f"{c.id:<28} {c.label}")
        return
    query, label = career_query(args.career, args.text, careers)
    recs = Recommender(load_catalog()).recommend(query, args.program, top_n=args.top, goal_label=label)
    print(f"Top {len(recs)} for {label} ({args.program}). Scores are word-match strength, 0-100.")
    if not recs or recs[0].score < WEAK_SCORE:
        print("Weak matches: this year's catalog has few courses on this topic. Talk to your advisor.")
    print()
    for i, r in enumerate(recs, 1):
        toward = f"  [counts toward: {'; '.join(r.counts_toward)}]" if r.counts_toward else "  [free elective]"
        print(f"{i}. {r.course_id} {r.title}  score {r.score}{toward}")
        print(textwrap.fill(r.reason, 100, initial_indent="   ", subsequent_indent="   "))


if __name__ == "__main__":
    main()
