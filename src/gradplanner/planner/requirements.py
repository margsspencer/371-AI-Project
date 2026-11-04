"""What a student still needs, in a form both the solver and the plan checker use.

This module only describes requirements (which courses could count, how many
are needed). It does not decide anything. The solver (model.py) and the
checker (validate_plan.py) each enforce these rules with their own code, so a
bug in one is caught by the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from gradplanner import config
from gradplanner.models import Catalog, CoreCategory, Course, ManualAtom, Program, RequirementItem
from gradplanner.parser.prereq import cnf_satisfied
from gradplanner.planner.student import StudentProfile


@dataclass
class PlannerSettings:
    placeholder_hours: dict[str, float]
    subject_departments: dict[str, str]

    def slot_hours(self, category_id: str) -> float:
        return self.placeholder_hours.get(category_id, self.placeholder_hours.get("default", 3))


def load_settings(overrides_dir=config.OVERRIDES_DIR) -> PlannerSettings:
    team = yaml.safe_load((overrides_dir / "team_decisions.yaml").read_text()) or {}
    hours = {k: float(v) for k, v in (team.get("placeholder_hours") or {}).items() if k != "source"}
    return PlannerSettings(hours or {"default": 3.0}, dict(team.get("subject_departments") or {}))


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


def program_needs(program: Program) -> list[ProgramNeed]:
    listed = {c for g in program.groups for it in g.items if it.kind in ("course", "choose") for c in it.options}
    needs = []
    for gi, group in enumerate(program.groups):
        for ii, item in enumerate(group.items):
            needs.append(ProgramNeed(f"{program.id}:{gi}.{ii}", f"{group.label}: {item.label}", item,
                                     listed if item.kind == "hours" else set()))
    return needs


def countable_hours(course: Course, program_id: str, catalog: Catalog) -> float:
    """Hours this course can count toward the program (0 if the Bulletin says it may not count)."""
    excluded = catalog.excluded_from(program_id)
    if course.id not in excluded:
        return course.credits
    cap = excluded[course.id]
    return 0.0 if cap is None else min(course.credits, cap)


def fits_hours_rule(course: Course, need: ProgramNeed, program_id: str, catalog: Catalog) -> bool:
    it = need.item
    return (it.kind == "hours" and course.subject == it.subject and course.level >= (it.min_level or 0)
            and course.id not in need.listed_courses and countable_hours(course, program_id, catalog) > 0)


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
    return {subj for subj, name in settings.subject_departments.items() if name in depts}


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
    for clause in course.prereq:
        if all(isinstance(a, ManualAtom) for a in clause):
            return "prerequisite needs a human check: " + " or ".join(str(a) for a in clause)
    return None
