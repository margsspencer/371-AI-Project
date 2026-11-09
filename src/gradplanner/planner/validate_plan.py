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
from gradplanner.planner.precheck import unplannable_needs
from gradplanner.planner.requirements import (
    ABROAD_COURSE_HOURS, PlannerSettings, abroad_problems, abroad_program_cap, core_needs, countable_hours, course_counts_for_core, fits_hours_rule, load_settings,
    program_problems, rule_or, schedulable_reason, student_needs,
)
from gradplanner.planner.student import StudentProfile

STANDING_RULES = {"sophomore": "standing_sophomore_hours", "junior": "standing_junior_hours",
                  "senior": "standing_senior_hours"}


def validate_plan(plan: Plan, catalog: Catalog, student: StudentProfile,
                  settings: PlannerSettings | None = None) -> list[str]:
    settings = settings or load_settings(catalog)
    problems: list[str] = list(program_problems(catalog, student, settings)) + abroad_problems(student)
    done = {c.course: c.source for c in student.finished if c.course in catalog.courses}

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
        problems += _abroad_term(tp, student, settings)
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
            why = _business(course, tp.term, student, catalog, settings)
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
                elif item.kind in ("core_slot", "abroad"):
                    slots[k] = slots.get(k, 0) + 1

    # --- major / minor
    hours_rule_use: dict[tuple[str, str], list[str]] = {}
    advisor_only = unplannable_needs(catalog, student)     # the plan can't fill these; the audit shows them
    for need in student_needs(catalog, student):
        it = need.item
        pid = need.program_id
        excluded = catalog.excluded_from(pid)
        if need.key in advisor_only:
            continue
        if it.kind in ("course", "choose"):
            got = [o for o in it.options if o in everything and not (o in excluded and excluded[o] is None)]
            if len(got) < it.n:
                problems.append(f"Not met: {need.label} (need {it.n} of {', '.join(it.options)}).")
        elif it.kind == "hours":
            total = slots.get(need.key, 0) * ABROAD_COURSE_HOURS            # abroad courses toward this rule
            total += sum(o.hours for o in student.outside_courses if o.counts_for == need.key)   # earned abroad
            for cid in counts.get(need.key, []):
                course = catalog.courses[cid]
                if not fits_hours_rule(course, need, pid, catalog):
                    problems.append(f"{cid} can't count toward {need.label}.")
                    continue
                hours_rule_use.setdefault((cid, pid), []).append(need.label)
                total += countable_hours(course, pid, catalog)
            if total < (it.hours or 0):
                problems.append(f"Not met: {need.label} ({total:g} of {it.hours:g} hours).")
    problems += _abroad_limits(plan, catalog, student, settings)
    for (cid, _), labels in hours_rule_use.items():
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
                if any(need.id in i.counts_toward for i in tp.items if i.kind in ("core_slot", "abroad")) \
                        and student.year_index(tp.term) > deadline:
                    problems.append(f"{tp.term}: {need.category.name} is planned too late ({need.category.timing}).")
        if need.one_per_term:
            for tp in plan.terms:
                if sum(1 for i in tp.items if i.kind in ("core_slot", "abroad") and need.id in i.counts_toward) > 1:
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


def _business(course, term, student: StudentProfile, catalog: Catalog, settings: PlannerSettings) -> str | None:
    """School of Business timing, checked here with its own code (not the planner's helper)."""
    rule = settings.business
    if not rule or course.subject not in rule.subjects:
        return None
    for prog in catalog.programs.values():
        for g in prog.groups:
            if "admission" in g.label.lower() and any(course.id in it.options for it in g.items):
                return None                       # an admission prerequisite: open before admission
    business = any(catalog.programs[p].school == "Business" or p in rule.extra_programs for p in student.programs)
    if not business:
        required = any(course.id in it.options for p in student.programs for g in catalog.programs[p].groups
                       for it in g.items if it.kind in ("course", "choose"))
        return None if required else "is a School of Business course (needs instructor permission)"
    if _year_season(term, student) < _admitted(settings):
        return "is a School of Business course planned before admission (sophomore spring)"
    return None


SEASONS = ["fall", "spring", "summer"]


def _year_season(term, student: StudentProfile) -> tuple[int, int]:
    """(school year 1-4+, season position) of a term for this student."""
    return term.academic_year - student.entry_term.academic_year + 1, SEASONS.index(term.season)


def _admitted(settings: PlannerSettings) -> tuple[int, int]:
    rule = settings.business
    return (rule.admitted_year, SEASONS.index(rule.admitted_season)) if rule else (0, 0)


def _abroad_term(tp, student: StudentProfile, settings: PlannerSettings) -> list[str]:
    """An abroad semester has only abroad courses and a full-time load; other semesters have none."""
    a = student.abroad_in(tp.term)
    kinds = {i.kind for i in tp.items}
    if a is None:
        return [f"{tp.term}: abroad courses in a semester on campus."] if "abroad" in kinds else []
    out = []
    if kinds - {"abroad"}:
        out.append(f"{tp.term}: Wake Forest catalog courses or slots planned while abroad.")
    if tp.hours < settings.abroad.min_hours:
        out.append(f"{tp.term}: {tp.hours:g} hours abroad is below a full-time load ({settings.abroad.min_hours:g}).")
    for item in tp.items:
        for k in item.counts_toward:
            if ":" in k:
                continue                                  # a major/minor rule (limits checked below)
            if k not in settings.abroad.categories:
                out.append(f"{tp.term}: an abroad course can't count toward {k}.")
            elif a.program == "affiliate" and k.startswith("21CS"):
                out.append(f"{tp.term}: affiliate (transfer) credit can't count toward {k}.")
    return out


def _abroad_limits(plan: Plan, catalog: Catalog, student: StudentProfile, settings: PlannerSettings) -> list[str]:
    out = []
    per_program: dict[tuple[str, bool], float] = {}
    affiliate_hours = 0.0
    for tp in plan.terms:
        a = student.abroad_in(tp.term)
        if a is None:
            continue
        if a.program == "affiliate":
            affiliate_hours += tp.hours
        for item in tp.items:
            for k in item.counts_toward:
                if ":" in k:
                    pid = k.split(":", 1)[0]
                    per_program[(pid, False)] = per_program.get((pid, False), 0) + item.hours
                    if a.program == "affiliate":
                        per_program[(pid, True)] = per_program.get((pid, True), 0) + item.hours
    for (pid, affiliate), hours in per_program.items():
        cap = abroad_program_cap(catalog, pid, settings, affiliate)
        if hours > cap:
            kind = "affiliate-program " if affiliate else ""
            out.append(f"{hours:g} {kind}abroad hours count toward {catalog.programs[pid].name}; the limit is {cap:g}.")
    if affiliate_hours:
        earned = (sum(catalog.courses[c.course].credits for c in student.finished
                      if c.source != "wfu" and c.course in catalog.courses)
                  + sum(o.hours for o in student.outside_courses if o.source != "wfu"))
        limit = rule_or(catalog, "max_outside_hours", 30)
        if earned + affiliate_hours > limit:
            out.append(f"{earned + affiliate_hours:g} hours of outside credit (AP, transfer, affiliate abroad); "
                       f"at most {limit:g} count.")
    return out
