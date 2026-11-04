"""Check a plan with plain Python loops, independently of the solver.

The solver should only return valid plans. This checker re-tests every rule
in the simplest possible way, so the two methods have to agree. It also
works on hand-edited plans (e.g. after a student moves a course in the UI).

`validate_plan` returns a list of problems in plain English. An empty list
means the plan is valid.
"""

from __future__ import annotations

from gradplanner.models import Catalog
from gradplanner.parser.prereq import cnf_satisfied, cnf_to_text, unmet_clauses
from gradplanner.planner.plan import Plan
from gradplanner.planner.requirements import (
    PlannerSettings, core_needs, countable_hours, course_counts_for_core, fits_hours_rule, load_settings,
    program_needs, rule_or, schedulable_reason,
)
from gradplanner.planner.student import StudentProfile

STANDING_RULES = {"sophomore": "standing_sophomore_hours", "junior": "standing_junior_hours",
                  "senior": "standing_senior_hours"}


def validate_plan(plan: Plan, catalog: Catalog, student: StudentProfile,
                  settings: PlannerSettings | None = None) -> list[str]:
    settings = settings or load_settings()
    problems: list[str] = []
    program = catalog.programs[student.program]
    done = {c.course: c.source for c in student.completed if c.course in catalog.courses}

    # --- terms are the student's terms, in order, within the deadline
    allowed = student.plan_terms()
    for tp in plan.terms:
        if tp.term not in allowed:
            problems.append(f"{tp.term} is outside the planning window ({allowed[0]} to {allowed[-1]}).")
    if [tp.term for tp in plan.terms] != sorted(tp.term for tp in plan.terms):
        problems.append("Terms are not in order.")

    # --- each course once, offered, schedulable, prerequisites/corequisites, standing, load
    seen: dict[str, str] = {}
    have_before: set[str] = set(done)               # courses finished before the current term
    hours_before = sum(catalog.courses[c].credits for c in done) + sum(o.hours for o in student.outside_courses)
    bulletin_max = rule_or(catalog, "max_hours_per_semester", 17)
    for tp in plan.terms:
        this_term = set(tp.course_ids)
        for cid in tp.course_ids:
            course = catalog.courses.get(cid)
            if course is None:
                problems.append(f"{tp.term}: {cid} is not in the catalog.")
                continue
            if cid in done:
                problems.append(f"{tp.term}: {cid} is already completed.")
            if cid in seen:
                problems.append(f"{tp.term}: {cid} is also planned in {seen[cid]}.")
            seen[cid] = str(tp.term)
            if not course.is_offered(tp.term.season, tp.term.year):
                problems.append(f"{tp.term}: {cid} is not offered then.")
            why = schedulable_reason(course)
            if why:
                problems.append(f"{tp.term}: {cid} {why}.")
            if not cnf_satisfied(course.prereq, have_before):
                problems.append(f"{tp.term}: {cid} prerequisite not met: "
                                f"{cnf_to_text(unmet_clauses(course.prereq, have_before))}.")
            if not cnf_satisfied(course.coreq, have_before | this_term):
                problems.append(f"{tp.term}: {cid} corequisite not met: {cnf_to_text(course.coreq)}.")
            if course.min_standing in STANDING_RULES:
                needed = rule_or(catalog, STANDING_RULES[course.min_standing], 0)
                if hours_before < needed:
                    problems.append(f"{tp.term}: {cid} needs {course.min_standing} standing "
                                    f"({needed:g} h); you'd have {hours_before:g} h.")
        limit = student.max_hours(tp.term, bulletin_max)
        if tp.hours > limit:
            problems.append(f"{tp.term}: {tp.hours:g} hours is over the limit of {limit:g}.")
        have_before |= this_term
        hours_before += tp.hours

    everything = set(have_before)                  # completed + all planned courses
    for cid in everything:
        for other in catalog.courses[cid].exclusive_with:
            if other in everything and cid < other:
                problems.append(f"Credit is not allowed for both {cid} and {other}.")

    # --- what each course counts toward: one (key -> courses) map for the whole plan
    counts: dict[str, list[str]] = {}
    for cid, keys in plan.completed_counts.items():
        for k in keys:
            counts.setdefault(k, []).append(cid)
    slots: dict[str, int] = {}
    for tp in plan.terms:
        for item in tp.items:
            for k in item.counts_toward:
                if item.kind == "course":
                    counts.setdefault(k, []).append(item.id)
                elif item.kind == "core_slot":
                    slots[k] = slots.get(k, 0) + 1

    # --- major / minor
    excluded = catalog.excluded_from(program.id)
    hours_rule_use: dict[str, list[str]] = {}
    for need in program_needs(program):
        it = need.item
        if it.kind in ("course", "choose"):
            got = [o for o in it.options if o in everything and not (o in excluded and excluded[o] is None)]
            if len(got) < it.n:
                problems.append(f"Not met: {need.label} (need {it.n} of {', '.join(it.options)}).")
        elif it.kind == "hours":
            total = 0.0
            for cid in counts.get(need.key, []):
                course = catalog.courses[cid]
                if not fits_hours_rule(course, need, program.id, catalog):
                    problems.append(f"{cid} can't count toward {need.label}.")
                    continue
                hours_rule_use.setdefault(cid, []).append(need.label)
                total += countable_hours(course, program.id, catalog)
            if total < (it.hours or 0):
                problems.append(f"Not met: {need.label} ({total:g} of {it.hours:g} hours).")
    for cid, labels in hours_rule_use.items():
        if len(labels) > 1:
            problems.append(f"{cid} is counted toward more than one hours rule: {'; '.join(labels)}.")

    # --- core curriculum
    needs = core_needs(catalog, student, settings)
    per_course_kind: dict[tuple[str, str], int] = {}
    for need in needs:
        courses = counts.get(need.id, [])
        for cid in courses:
            if not course_counts_for_core(catalog.courses[cid], need, catalog, settings, done.get(cid, "wfu")):
                problems.append(f"{cid} can't count toward {need.category.name}.")
            per_course_kind[(cid, need.kind)] = per_course_kind.get((cid, need.kind), 0) + 1
        if need.kind == "division" and need.count > 1:
            subjects = [c.split()[0] for c in courses]
            if len(subjects) != len(set(subjects)):
                problems.append(f"{need.category.name}: two courses from the same department.")
        have = len(courses) + slots.get(need.id, 0)
        if have < need.count:
            problems.append(f"Not met: {need.category.name} ({have} of {need.count}).")
        if need.last_year is not None:
            first_index = student.year_index(allowed[0])
            deadline = max(need.last_year, first_index)
            for tp in plan.terms:
                if need.id in [i.id for i in tp.items if i.kind == "core_slot"] \
                        and student.year_index(tp.term) > deadline:
                    problems.append(f"{tp.term}: {need.category.name} is planned too late ({need.category.timing}).")
        if need.one_per_term:
            for tp in plan.terms:
                if sum(1 for i in tp.items if i.kind == "core_slot" and i.id == need.id) > 1:
                    problems.append(f"{tp.term}: more than one {need.category.name} course in one term.")
    for (cid, kind), n in per_course_kind.items():
        if kind in ("division", "21cs") and n > 1:
            problems.append(f"{cid} is counted toward more than one {kind} category.")

    # --- 120 hours
    earned = (sum(catalog.courses[c].credits for c in done) + sum(o.hours for o in student.outside_courses)
              + sum(tp.hours for tp in plan.terms))
    total = rule_or(catalog, "total_hours", 120)
    if earned < total:
        problems.append(f"Only {earned:g} of {total:g} hours by graduation.")
    return problems
