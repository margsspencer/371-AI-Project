"""What a student still needs, in a form both the solver and the plan checker use.

This module only describes requirements (which courses could count, how many
are needed). It does not decide anything. The solver (model.py) and the
checker (validate_plan.py) each enforce these rules with their own code, so a
bug in one is caught by the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from gradplanner import config
from gradplanner.models import Catalog, CoreCategory, Course, ManualAtom, Program, RequirementItem
from gradplanner.parser.prereq import cnf_satisfied
from gradplanner.planner.student import StudentProfile


@dataclass
class BusinessRule:
    subjects: set[str]
    admitted_year: int = 2              # 1 = first year, 2 = sophomore, ...
    admitted_season: str = "spring"
    extra_programs: set[str] = field(default_factory=set)


@dataclass
class AbroadRules:
    min_hours: float = 12
    default_program_hours: float = 6
    business_program_hours: float = 6
    subject_hours: dict[str, dict] = field(default_factory=dict)   # {"SOC": {"hours": 6, ...}}
    categories: set[str] = field(default_factory=set)               # core categories abroad courses may fill
    notes: list[str] = field(default_factory=list)


@dataclass
class PlannerSettings:
    placeholder_hours: dict[str, float]
    subject_departments: dict[str, str]
    business: BusinessRule | None = None
    not_double_majors: list[tuple[set[str], set[str], str]] = field(default_factory=list)
    abroad: AbroadRules = field(default_factory=AbroadRules)

    def slot_hours(self, category_id: str) -> float:
        return self.placeholder_hours.get(category_id, self.placeholder_hours.get("default", 3))


def load_settings(catalog: Catalog | None = None, overrides_dir=config.OVERRIDES_DIR) -> PlannerSettings:
    """Planner settings from team_decisions.yaml. Subject -> department names come from the
    catalog's subject list (Courses A-Z), with the team file's entries taking precedence."""
    team = yaml.safe_load((overrides_dir / "team_decisions.yaml").read_text()) or {}
    hours = {k: float(v) for k, v in (team.get("placeholder_hours") or {}).items() if k != "source"}
    departments = dict(catalog.subjects) if catalog else {}
    departments.update(team.get("subject_departments") or {})
    business = None
    if b := team.get("business_school"):
        when = b.get("admitted_term") or {}
        business = BusinessRule(set(b.get("subjects") or []), int(when.get("year", 2)),
                                str(when.get("season", "spring")), set(b.get("extra_programs") or []))
    pairs = [(set(r["a"]), set(r["b"]), r.get("source", "")) for r in team.get("not_double_majors") or []]
    a = team.get("study_abroad") or {}
    abroad = AbroadRules(float(a.get("min_hours", 12)), float(a.get("default_program_hours", 6)),
                         float(a.get("business_program_hours", 6)), dict(a.get("subject_hours") or {}),
                         set(a.get("categories") or []), list(a.get("notes") or []))
    return PlannerSettings(hours or {"default": 3.0}, departments, business, pairs, abroad)


# ---------------------------------------------------------------------------
# Major / minor requirements
# ---------------------------------------------------------------------------


@dataclass
class ProgramNeed:
    key: str                 # "CSC-BS:0.3" (group 0, item 3)
    label: str               # "Required Major Courses: CSC 231"
    item: RequirementItem
    # For "hours" items: courses named elsewhere in the program. They are
    # required on their own, so they can't also fill an elective-hours rule.
    listed_courses: set[str] = field(default_factory=set)

    @property
    def program_id(self) -> str:
        return self.key.split(":", 1)[0]


def program_needs(program: Program) -> list[ProgramNeed]:
    listed = {c for g in program.groups for it in g.items if it.kind in ("course", "choose") for c in it.options}
    needs = []
    for gi, group in enumerate(program.groups):
        for ii, item in enumerate(group.items):
            needs.append(ProgramNeed(f"{program.id}:{gi}.{ii}", f"{group.label}: {item.label}", item,
                                     listed if item.kind == "hours" else set()))
    return needs


def student_needs(catalog: Catalog, student: StudentProfile) -> list[ProgramNeed]:
    """Requirements of every major and minor the student has, in order."""
    return [n for pid in student.programs for n in program_needs(catalog.programs[pid])]


def needed_courses(catalog: Catalog, student: StudentProfile) -> set[str]:
    """Courses named by a required-course or choose-list row in any of the student's programs."""
    return {o for n in student_needs(catalog, student) if n.item.kind in ("course", "choose") for o in n.item.options}


def program_problems(catalog: Catalog, student: StudentProfile, settings: PlannerSettings) -> list[str]:
    """Combinations the Bulletin doesn't allow (e.g. Biology with Neuroscience), and unknown ids."""
    out = []
    unknown = [p for p in student.programs if p not in catalog.programs]
    if unknown:
        return [f"Unknown program(s): {', '.join(unknown)}."]
    if len(set(student.programs)) != len(student.programs):
        out.append("The same program is listed twice.")
    majors = [p for p in student.programs if catalog.programs[p].type == "major"]
    for i, a in enumerate(majors):
        for b in majors[i + 1:]:
            for left, right, source in settings.not_double_majors:
                if (a in left and b in right) or (a in right and b in left):
                    out.append(f"{catalog.programs[a].name} and {catalog.programs[b].name} can't be a double "
                               f"major (Bulletin, {source}).")
                    break
    return out


ABROAD_COURSE_HOURS = 3


def abroad_program_cap(catalog: Catalog, program_id: str, settings: PlannerSettings, affiliate: bool) -> float:
    """Hours from study abroad that may count toward one major or minor."""
    rules, prog = settings.abroad, catalog.programs[program_id]
    business = settings.business
    if prog.school == "Business" or (business and program_id in business.extra_programs):
        return rules.business_program_hours
    sub = rules.subject_hours.get(prog.subject)
    if sub and (affiliate or not sub.get("affiliate_only")):
        return float(sub["hours"])
    return rules.default_program_hours


def abroad_category_ok(category: CoreCategory, settings: PlannerSettings, affiliate: bool) -> bool:
    """Can an abroad course count toward this core category? (No 21CS from transfer credit.)"""
    if category.id not in settings.abroad.categories:
        return False
    return not (affiliate and category.family == "21CS")


def abroad_problems(student: StudentProfile) -> list[str]:
    """Abroad semesters that can't work, in plain English."""
    out, terms = [], student.plan_terms()
    seen = set()
    for a in student.abroad:
        if a.term in seen:
            out.append(f"{a.term} is listed twice for study abroad.")
        seen.add(a.term)
        if a.term not in terms:
            out.append(f"Study abroad in {a.term} is outside the semesters being planned "
                       f"({terms[0]} to {terms[-1]})." if terms else "No semesters to plan.")
        elif a.program == "affiliate" and student.year_index(a.term) >= 3:
            out.append(f"{a.term} is in your senior year; the Bulletin requires senior-year work in "
                       "Wake Forest programs, so choose a Wake Forest program or an earlier semester.")
    return out


def admission_courses(catalog: Catalog) -> set[str]:
    """Courses listed under "Prerequisites for Admission" on business major pages (taken before admission)."""
    return {o for p in catalog.programs.values() for g in p.groups if "admission" in g.label.lower()
            for it in g.items for o in it.options}


def is_business_student(catalog: Catalog, student: StudentProfile, settings: PlannerSettings) -> bool:
    rule = settings.business
    return bool(rule) and any(catalog.programs[p].school == "Business" or p in rule.extra_programs
                              for p in student.programs if p in catalog.programs)


def before_admission(term, student: StudentProfile, rule: BusinessRule) -> bool:
    """True if `term` comes before the student's School of Business admission term."""
    order = {"fall": 0, "spring": 1, "summer": 2}                     # order within a school year
    year = student.year_index(term) + 1
    return (year, order[term.season]) < (rule.admitted_year, order[rule.admitted_season])


def business_reason(course: Course, term, student: StudentProfile, catalog: Catalog,
                    settings: PlannerSettings, admission: set[str] | None = None) -> str | None:
    """Why a School of Business course can't go in `term` (None if it can).

    Business majors: not before admission (sophomore spring), except admission prerequisites.
    Other students need instructor permission, so only courses their programs require are allowed."""
    rule = settings.business
    if not rule or course.subject not in rule.subjects:
        return None
    admission = admission_courses(catalog) if admission is None else admission
    if course.id in admission:
        return None
    if is_business_student(catalog, student, settings):
        if before_admission(term, student, rule):
            return "is a School of Business course; business majors take these after admission (sophomore spring)"
        return None
    if course.id in needed_courses(catalog, student):
        return None
    return "is a School of Business course; non-business students need instructor permission"


def countable_hours(course: Course, program_id: str, catalog: Catalog) -> float:
    """Hours this course can count toward the program (0 if the Bulletin says it may not count)."""
    excluded = catalog.excluded_from(program_id)
    if course.id not in excluded:
        return course.credits
    cap = excluded[course.id]
    return 0.0 if cap is None else min(course.credits, cap)


def fits_hours_rule(course: Course, need: ProgramNeed, program_id: str, catalog: Catalog) -> bool:
    it = need.item
    if it.kind != "hours" or countable_hours(course, program_id, catalog) <= 0:
        return False
    if it.options:                        # "Select 9 hours of the following: ..."
        return course.id in it.options
    return (course.subject in (it.subject or "").split("|") and course.level >= (it.min_level or 0)
            and course.id not in need.listed_courses)


# ---------------------------------------------------------------------------
# Core Curriculum
# ---------------------------------------------------------------------------

TIMING_LAST_YEAR = {"year1": 0, "by_end_of_year2": 1}   # last allowed year index (0 = first year)


@dataclass
class CoreNeed:
    category: CoreCategory
    count: int                  # courses still needed
    slot_hours: float           # hours of one generic slot
    kind: str                   # slot | division | 21cs | tag
    last_year: int | None       # timing rule: latest year index a slot may go in
    one_per_term: bool = False  # language courses build on each other

    @property
    def id(self) -> str:
        return self.category.id


def division_subjects(category: CoreCategory, settings: PlannerSettings) -> set[str]:
    depts = category.departments or ""
    return {subj for subj, name in settings.subject_departments.items()
            if name and re.search(rf"\b{re.escape(name)}\b", depts)}


def course_counts_for_core(course: Course, need: CoreNeed, catalog: Catalog, settings: PlannerSettings,
                           source: str = "wfu") -> bool:
    """Can this course count toward this core category? (AP/transfer never count for divisional or 21CS.)"""
    tag = need.category.tag
    if need.kind == "division":
        return (source == "wfu" and "D" in course.tags
                and course.subject in division_subjects(need.category, settings)
                and course.credits >= rule_or(catalog, "min_hours_for_core_course", 3))
    if need.kind == "21cs":
        return source == "wfu" and tag in course.tags
    if need.kind == "tag":
        return tag in course.tags
    return False


def rule_or(catalog: Catalog, key: str, default: float) -> float:
    """A degree rule's value, or `default` if the Bulletin rule wasn't found (validation flags that)."""
    rule = catalog.rules.get(key)
    return rule.value if rule else default


def core_needs(catalog: Catalog, student: StudentProfile, settings: PlannerSettings) -> list[CoreNeed]:
    """Core categories with the number of courses still needed after outside courses are counted."""
    needs = []
    for cat in catalog.core:
        required = student.language_courses_needed if cat.id == "BASIC-LANG" else cat.courses_required
        kind = {"basic": "slot", "divisional": "division", "21CS": "21cs"}.get(cat.family, "tag")
        if cat.family == "basic" and cat.course_cnf and cnf_satisfied(cat.course_cnf, student.completed_ids):
            required = 0
        for oc in student.outside_courses:
            if oc.counts_for == cat.id and not (oc.source != "wfu" and kind in ("division", "21cs")):
                required -= 1
        needs.append(CoreNeed(cat, max(0, required), settings.slot_hours(cat.id), kind,
                              TIMING_LAST_YEAR.get(cat.timing or ""), one_per_term=(cat.id == "BASIC-LANG")))
    return needs


def schedulable_reason(course: Course) -> str | None:
    """Why the planner won't place this course on its own, or None if it can."""
    if course.override_required:
        return "needs department permission"
    if course.credits_min is None:
        return "credit hours unknown"
    if not course.offerings:
        return "no offering pattern"
    for kind, cnf in (("prerequisite", course.prereq), ("corequisite", course.coreq)):
        for clause in cnf:
            if all(isinstance(a, ManualAtom) for a in clause):
                return f"{kind} needs a human check: " + " or ".join(str(a) for a in clause)
    return None
