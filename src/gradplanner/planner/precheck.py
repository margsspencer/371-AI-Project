"""Quick checks before the solver runs, so "no valid plan" comes with a plain reason.

These are simple loops, not the solver. They catch the common problems:
  - a required course the planner can't schedule (needs permission, not in our data):
    not a blocker; the requirement is left for the advisor and the rest is planned
  - a prerequisite chain longer than the semesters left
    ("CSC 301 needs CSC 111 -> CSC 112 -> CSC 201 -> CSC 301: 4 semesters, but only 3 remain")
  - not enough room for 120 hours even at the maximum load
"""

from __future__ import annotations

import math

from gradplanner.models import Catalog, CourseAtom, LevelAtom
from gradplanner.planner.requirements import PlannerSettings, program_needs, rule_or, schedulable_reason
from gradplanner.planner.student import StudentProfile
from gradplanner.planner.terms import Term


def chain_length(catalog: Catalog, course_id: str, done: set[str], memo: dict | None = None,
                 visiting: set | None = None) -> tuple[float, list[str]]:
    """Fewest semesters needed to finish `course_id`, and the longest prerequisite path.

    Completed courses take 0 semesters. Courses the planner can't schedule take
    infinitely many. Corequisites and offerings are ignored, so this is a lower bound.
    """
    memo = {} if memo is None else memo
    visiting = set() if visiting is None else visiting
    if course_id in done:
        return 0, []
    if course_id in memo:
        return memo[course_id]
    course = catalog.courses.get(course_id)
    if course is None or schedulable_reason(course) or course_id in visiting:
        return math.inf, [course_id]
    visiting.add(course_id)
    longest, path = 0.0, []
    for clause in course.prereq:
        best, best_path = math.inf, []
        for atom in clause:
            if isinstance(atom, CourseAtom):
                n, p = chain_length(catalog, atom.code, done, memo, visiting)
            elif isinstance(atom, LevelAtom):
                matches = [c for c in catalog.courses.values()
                           if c.subject == atom.subject and c.level >= atom.min_level and c.id != course_id]
                options = [chain_length(catalog, c.id, done, memo, visiting) for c in matches]
                n, p = min(options, key=lambda x: x[0]) if options else (math.inf, [])
            else:
                n, p = math.inf, []
            if n < best:
                best, best_path = n, p
        if best > longest:
            longest, path = best, best_path
    visiting.discard(course_id)
    memo[course_id] = (longest + 1, path + [course_id])
    return memo[course_id]


def unplannable_needs(catalog: Catalog, student: StudentProfile) -> dict[str, str]:
    """{requirement key: reason} for course/choose requirements the planner can't fill on its own
    (every option needs permission, a human-checked prerequisite, or isn't in our data)."""
    done = {c for c in student.completed_ids if c in catalog.courses}
    program = catalog.programs[student.program]
    excluded = catalog.excluded_from(program.id)
    memo: dict = {}
    out = {}
    for need in program_needs(program):
        it = need.item
        if it.kind not in ("course", "choose") and not (it.kind == "hours" and it.options):
            continue
        options = [o for o in it.options if not (o in excluded and excluded[o] is None)]
        usable = [o for o in options if chain_length(catalog, o, done, memo)[0] != math.inf]
        if it.kind == "hours":
            short = sum(catalog.courses[o].credits for o in usable) < (it.hours or 0)
        else:
            short = len(usable) < it.n
        if short:
            reasons = []
            for o in options:
                if o in usable:
                    continue
                course = catalog.courses.get(o)
                why = "not in our data" if course is None else schedulable_reason(course)
                if why is None:
                    why = f"depends on {chain_length(catalog, o, done, memo)[1][0]}, which can't be scheduled"
                reasons.append(f"{o}: {why}")
            out[need.key] = (f"{need.label} can't be planned automatically ({'; '.join(reasons)}). "
                             "Ask your advisor about permission or a substitute.")
    return out


def precheck(catalog: Catalog, student: StudentProfile, settings: PlannerSettings,
             terms: list[Term]) -> tuple[list[str], list[str]]:
    """Return (blocking problems, notes). Any blocking problem means no plan is possible."""
    blocking: list[str] = []
    notes: list[str] = []
    done = {c for c in student.completed_ids if c in catalog.courses}
    program = catalog.programs[student.program]
    excluded = catalog.excluded_from(program.id)
    memo: dict = {}

    if not terms:
        return ["There are no semesters between next_term and graduation_term."], notes
    notes += list(unplannable_needs(catalog, student).values())

    for need in program_needs(program):
        it = need.item
        if it.kind not in ("course", "choose"):
            continue
        options = [o for o in it.options if not (o in excluded and excluded[o] is None)]
        lengths = sorted((chain_length(catalog, o, done, memo) + (o,) for o in options), key=lambda x: x[0])
        usable = [x for x in lengths if x[0] != math.inf]
        if len(usable) < it.n:
            continue                      # left for the advisor (see unplannable_needs)
        need_terms, path, _ = usable[it.n - 1]
        if need_terms > len(terms):
            blocking.append(f"{need.label}: the prerequisite chain {' -> '.join(path)} takes "
                            f"{need_terms:g} semesters, but only {len(terms)} remain before {terms[-1]}.")

    bulletin_max = rule_or(catalog, "max_hours_per_semester", 17)
    capacity = sum(student.max_hours(t, bulletin_max) for t in terms)
    earned = sum(catalog.courses[c].credits for c in done) + sum(o.hours for o in student.outside_courses)
    total = rule_or(catalog, "total_hours", 120)
    if earned + capacity < total:
        blocking.append(f"Even at the maximum load you would have {earned + capacity:g} of {total:g} hours "
                        f"by {terms[-1]}. Add semesters, summer terms, or raise the hour limit.")

    outside = sum(catalog.courses[c.course].credits for c in student.finished
                  if c.source != "wfu" and c.course in catalog.courses)
    outside += sum(o.hours for o in student.outside_courses if o.source != "wfu")
    if outside > rule_or(catalog, "max_outside_hours", 30):
        notes.append(f"You list {outside:g} AP/transfer hours; at most "
                     f"{rule_or(catalog, 'max_outside_hours', 30):g} can count toward graduation.")
    return blocking, notes
