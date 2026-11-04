"""Degree audit: a checklist of every requirement and where the student stands.

Each line gets one status:
  done         met by completed courses
  in_progress  met once this semester's courses are passed
  planned      met by courses (or core slots) in the plan
  remaining    not met yet (the detail says how far along, e.g. "6 of 12 h")
  review       can't be checked automatically; ask an advisor

The audit works without the solver. It matches courses to requirements with
simple rules, preferring courses that are already done:
  - a required course or "choose" list: the earliest-finished options
  - elective-hours rules ("12 h of CSC 300+"): the strictest rule picks
    first, and each course fills only one rule
  - divisions: D-tagged courses (3+ h) from the division's departments,
    two different departments when two courses are needed, no AP/transfer
  - 21CS: each course counts for only one category, no AP/transfer
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gradplanner.models import Catalog, CourseAtom
from gradplanner.planner.plan import Plan
from gradplanner.planner.requirements import (
    CoreNeed, PlannerSettings, core_needs, countable_hours, course_counts_for_core, fits_hours_rule,
    load_settings, program_needs, rule_or,
)
from gradplanner.planner.student import StudentProfile

STAGES = ("done", "in_progress", "planned")          # best first
SECTIONS = {"basic": "Core: Basic", "division": "Core: Divisional", "21cs": "Core: 21st Century Stewardship",
            "tag": "Core: Other"}
_FAMILY_KIND = {"basic": "basic", "divisional": "division", "21CS": "21cs"}


@dataclass
class Entry:
    """One thing that could count: a catalog course, an outside course, or a plan slot."""
    name: str                  # "CSC 201", "HST 101", "Division I slot"
    stage: str                 # done | in_progress | planned
    hours: float
    course_id: str | None = None    # set for catalog courses
    source: str = "wfu"             # wfu | ap | transfer
    counts_for: str | None = None   # outside courses / slots: the core category they were entered for
    term: str | None = None

    @property
    def label(self) -> str:
        where = self.term if self.stage == "planned" and self.term else self.stage.replace("_", " ")
        return f"{self.name} ({where})"


@dataclass
class AuditLine:
    section: str
    label: str
    status: str
    detail: str
    used: list[Entry] = field(default_factory=list)


@dataclass
class Audit:
    student_name: str
    program_id: str
    lines: list[AuditLine]
    hours: dict[str, float]          # hours by stage
    notes: list[str] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for line in self.lines if line.status == status)


def _status(used: list[Entry]) -> str:
    """A requirement is only as finished as the last course it needs."""
    return max((e.stage for e in used), key=STAGES.index) if used else "done"


def _first(entries: list[Entry]) -> list[Entry]:
    return sorted(entries, key=lambda e: STAGES.index(e.stage))


def _entries(catalog: Catalog, student: StudentProfile, plan: Plan | None, notes: list[str]) -> list[Entry]:
    out = []
    for c in student.completed:
        if c.course in catalog.courses:
            out.append(Entry(c.course, "done", catalog.courses[c.course].credits, c.course, c.source))
        else:
            notes.append(f"{c.course} is not in our catalog; list it under outside_courses to count it.")
    for cid in student.in_progress:
        if cid in catalog.courses and cid not in {c.course for c in student.completed}:
            out.append(Entry(cid, "in_progress", catalog.courses[cid].credits, cid))
    for oc in student.outside_courses:
        out.append(Entry(oc.name, "done", oc.hours, None, oc.source, oc.counts_for))
    if plan:
        for tp in plan.terms:
            for item in tp.items:
                if item.kind == "course":
                    out.append(Entry(item.id, "planned", item.hours, item.id, term=str(tp.term)))
                else:
                    cat = item.id if item.kind == "core_slot" else None
                    name = "Free elective" if cat is None else f"{plan.labels.get(cat, cat)} course"
                    out.append(Entry(name, "planned", item.hours, None, counts_for=cat, term=str(tp.term)))
    return out


# ---------------------------------------------------------------------------
# Major / minor
# ---------------------------------------------------------------------------


def _program_lines(catalog: Catalog, student: StudentProfile, entries: list[Entry]) -> list[AuditLine]:
    program = catalog.programs[student.program]
    excluded = catalog.excluded_from(program.id)
    courses = [e for e in entries if e.course_id]
    lines: dict[str, AuditLine] = {}
    needs = program_needs(program)

    for need in needs:
        it = need.item
        section = "Major: " + need.label.split(":")[0] if program.type == "major" else "Minor"
        label = need.label.split(": ", 1)[-1]
        if it.kind in ("course", "choose"):
            options = [o for o in it.options if not (o in excluded and excluded[o] is None)]
            used = _first([e for e in courses if e.course_id in options])[:it.n]
            if len(used) >= it.n:
                lines[need.key] = AuditLine(section, label, _status(used), ", ".join(e.label for e in used), used)
            else:
                lines[need.key] = AuditLine(section, label, "remaining",
                                            f"{len(used)} of {it.n}" if it.n > 1 else "not taken", used)
            if it.free_text_options and lines[need.key].status == "remaining":
                lines[need.key].detail += "; other options need advisor approval"
        elif it.kind == "free_text":
            lines[need.key] = AuditLine(section, label, "review", "check with your advisor")
        elif it.kind == "note" and it.needs_review:
            lines[need.key] = AuditLine(section, label, "review", it.needs_review)

    # Elective-hours rules: strictest (highest level) first; each course fills one rule.
    taken: set[str] = set()
    for need in sorted((n for n in needs if n.item.kind == "hours"), key=lambda n: -(n.item.min_level or 0)):
        it = need.item
        section = "Major: " + need.label.split(":")[0] if program.type == "major" else "Minor"
        pool = [e for e in _first(courses) if e.course_id not in taken
                and fits_hours_rule(catalog.courses[e.course_id], need, program.id, catalog)]
        used, total = [], 0.0
        for e in pool:
            if total >= (it.hours or 0):
                break
            used.append(e)
            total += countable_hours(catalog.courses[e.course_id], program.id, catalog)
            taken.add(e.course_id)
        label = need.label.split(": ", 1)[-1]
        if total >= (it.hours or 0):
            lines[need.key] = AuditLine(section, label, _status(used), ", ".join(e.label for e in used), used)
        else:
            lines[need.key] = AuditLine(section, label, "remaining", f"{total:g} of {it.hours:g} h", used)
    return [lines[n.key] for n in needs if n.key in lines]


# ---------------------------------------------------------------------------
# Core Curriculum
# ---------------------------------------------------------------------------


def _core_lines(catalog: Catalog, student: StudentProfile, entries: list[Entry],
                settings: PlannerSettings) -> list[AuditLine]:
    needs = {n.id: n for n in core_needs(catalog, student, settings)}
    lines: dict[str, AuditLine] = {}
    used_21cs: set[int] = set()            # ids of entries already used for a 21CS category

    def qualifies(e: Entry, need: CoreNeed) -> bool:
        if e.counts_for == need.id:                         # outside course or slot entered for this category
            return not (e.source != "wfu" and need.kind in ("division", "21cs"))
        if e.course_id:
            return course_counts_for_core(catalog.courses[e.course_id], need, catalog, settings, e.source)
        return False

    def finish(need: CoreNeed, used: list[Entry], required: int, extra: str = "") -> AuditLine:
        section = SECTIONS[need.kind if need.kind != "slot" else "basic"]
        name = need.category.name
        if len(used) >= required:
            return AuditLine(section, name, _status(used), ", ".join(e.label for e in used) + extra, used)
        return AuditLine(section, name, "remaining", f"{len(used)} of {required}" + extra, used)

    # 21CS: categories with the fewest candidates pick first, so one course isn't wasted.
    order = sorted(needs.values(), key=lambda n: (n.kind == "21cs",
                                                  sum(qualifies(e, n) for e in entries) if n.kind == "21cs" else 0))
    for need in order:
        cat = need.category
        required = cat.courses_required
        if need.kind == "slot":
            codes = {a.code for clause in cat.course_cnf for a in clause if isinstance(a, CourseAtom)}
            used = _first([e for e in entries if e.counts_for == cat.id or (e.course_id in codes)])
            if cat.id == "BASIC-LANG":
                # The profile says how many more language courses are needed (0 = done or placed out).
                still = student.language_courses_needed
                if still == 0:
                    done = [e for e in used if e.stage == "done"]
                    lines[cat.id] = AuditLine(SECTIONS["basic"], cat.name, "done",
                                              ", ".join(e.label for e in done) or "no more courses needed (profile)",
                                              done)
                else:
                    upcoming = [e for e in used if e.stage != "done"]
                    lines[cat.id] = finish(need, upcoming[:still], still, " language courses")
                continue
            lines[cat.id] = finish(need, used[:required], required)
        elif need.kind == "division":
            used, depts = [], set()
            for e in _first([e for e in entries if qualifies(e, need)]):
                dept = e.name.split()[0] if e.counts_for != cat.id or e.stage != "planned" else None
                if required > 1 and dept and dept in depts:
                    continue                                 # two courses must be from different departments
                used.append(e)
                if dept:
                    depts.add(dept)
                if len(used) == required:
                    break
            lines[cat.id] = finish(need, used, required)
        elif need.kind == "21cs":
            pool = [e for e in _first([e for e in entries if qualifies(e, need)]) if id(e) not in used_21cs]
            used = pool[:required]
            used_21cs.update(id(e) for e in used)
            lines[cat.id] = finish(need, used, required)
        else:
            used = _first([e for e in entries if qualifies(e, need)])[:required]
            extra = " (team rule; confirm with advisor)" if cat.source != "bulletin" else ""
            lines[cat.id] = finish(need, used, required, extra)
    return [lines[c.id] for c in catalog.core if c.id in lines]


# ---------------------------------------------------------------------------


def run_audit(catalog: Catalog, student: StudentProfile, plan: Plan | None = None,
              settings: PlannerSettings | None = None) -> Audit:
    """Build the checklist from completed, in-progress, and (if given) planned courses."""
    settings = settings or load_settings(catalog)
    notes: list[str] = []
    entries = _entries(catalog, student, plan, notes)
    lines = _program_lines(catalog, student, entries) + _core_lines(catalog, student, entries, settings)

    hours = {s: sum(e.hours for e in entries if e.stage == s) for s in STAGES}
    total = rule_or(catalog, "total_hours", 120)
    running, status = 0.0, "remaining"
    for stage in STAGES:
        running += hours[stage]
        if running >= total:
            status = stage
            break
    lines.append(AuditLine("Graduation", f"{total:g} total hours", status,
                           f"{hours['done']:g} done, {hours['in_progress']:g} in progress, "
                           f"{hours['planned']:g} planned"))

    outside = sum(e.hours for e in entries if e.source != "wfu")
    limit = rule_or(catalog, "max_outside_hours", 30)
    if outside > limit:
        notes.append(f"{outside:g} AP/transfer hours listed; at most {limit:g} count toward graduation.")
    return Audit(student.name, student.program, lines, hours, notes)
