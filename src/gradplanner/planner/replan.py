"""Re-planning (milestone 7): record how a semester went, plan again, and show what changed.

    record_term(student, plan, outcomes)  -> the student's profile after the plan's first semester
    diff_plans(old, new, outcomes)        -> what changed between the two plans, in plain English

A course can be passed, failed, or dropped (dropped also covers withdrawn). Only passed
courses become completed. Generic slots that were passed ("Division I course",
"Free elective", an abroad course) become finished outside courses that count for the
same requirement, so the next plan doesn't schedule them again.
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field

from gradplanner.planner.plan import Plan, PlanItem
from gradplanner.planner.student import CompletedCourse, OutsideCourse, StudentProfile
from gradplanner.planner.terms import Term

OUTCOMES = ("passed", "failed", "dropped")


def item_key(item: PlanItem, index: int) -> str:
    """A stable name for one item of a semester (courses by id; slots by position)."""
    return item.id if item.kind == "course" else f"{item.id}#{index}"


def record_term(student: StudentProfile, plan: Plan, outcomes: dict[str, str] | None = None) -> StudentProfile:
    """The profile after the plan's first semester. `outcomes` maps item keys (see item_key) to
    passed / failed / dropped; anything not listed is assumed passed."""
    if not plan.terms:
        raise ValueError("the plan has no semesters")
    outcomes = outcomes or {}
    bad = {k: v for k, v in outcomes.items() if v not in OUTCOMES}
    if bad:
        raise ValueError(f"outcomes must be one of {OUTCOMES}: {bad}")
    tp = plan.terms[0]
    new = copy.deepcopy(student)
    abroad = student.abroad_in(tp.term)
    # Current courses (before the plan's first term) are assumed passed when the term ends.
    done = {c.course for c in new.completed}
    new.completed += [CompletedCourse(c) for c in new.in_progress if c not in done]
    new.in_progress = []
    for i, item in enumerate(tp.items):
        if outcomes.get(item_key(item, i), "passed") != "passed":
            continue
        if item.kind == "course":
            new.completed.append(CompletedCourse(item.id))
            continue
        counts_for = item.counts_toward[0] if item.counts_toward else None
        source = "affiliate" if abroad and abroad.program == "affiliate" else "wfu"
        new.outside_courses.append(OutsideCourse(f"{item.title} ({tp.term})", item.hours, counts_for, source))
        if counts_for == "BASIC-LANG" and new.language_courses_needed > 0:
            new.language_courses_needed -= 1
    new.next_term = tp.term.next(new.include_summer)
    new.abroad = [a for a in new.abroad if not a.term < new.next_term]     # the abroad semester is over
    return new


# ---------------------------------------------------------------- diff

@dataclass
class Change:
    kind: str            # done | failed | retake | moved | added | removed | slots
    what: str            # "CSC 201 Data Structures" or "Division I course"
    old_term: Term | None = None
    new_term: Term | None = None
    detail: str = ""

    @property
    def sentence(self) -> str:
        if self.kind == "done":
            return f"{self.what}: done in {self.old_term}."
        if self.kind == "failed":
            return f"{self.what}: {self.detail} in {self.old_term}; not needed again, so it isn't re-planned."
        if self.kind == "retake":
            return f"{self.what}: {self.detail} in {self.old_term}; planned again in {self.new_term}."
        if self.kind == "moved":
            return f"{self.what}: moved from {self.old_term} to {self.new_term}."
        if self.kind == "added":
            return f"{self.what}: added in {self.new_term}."
        if self.kind == "removed":
            return f"{self.what}: no longer planned (was {self.old_term})."
        return f"{self.what}: {self.detail}."


@dataclass
class PlanDiff:
    changes: list[Change] = field(default_factory=list)
    unchanged: int = 0                    # courses in the same semester in both plans
    old_last: Term | None = None
    new_last: Term | None = None
    old_hours: float = 0
    new_hours: float = 0

    def of(self, *kinds: str) -> list[Change]:
        return [c for c in self.changes if c.kind in kinds]

    @property
    def summary(self) -> str:
        moved = len(self.of("moved", "retake"))
        added, removed = len(self.of("added")), len(self.of("removed"))
        parts = [f"{self.unchanged} course(s) stay where they were", f"{moved} moved", f"{added} added",
                 f"{removed} removed"]
        if self.old_last and self.new_last and self.old_last != self.new_last:
            parts.append(f"graduation moves from {self.old_last} to {self.new_last}")
        else:
            parts.append(f"still graduating {self.new_last}" if self.new_last else "no plan")
        return "; ".join(parts) + "."


def _courses(plan: Plan) -> dict[str, tuple[Term, PlanItem]]:
    return {i.id: (tp.term, i) for tp in plan.terms for i in tp.items if i.kind == "course"}


def _slots(plan: Plan, skip: Term | None = None) -> Counter:
    """{(term, slot title): count} for generic slots."""
    return Counter((tp.term, i.title) for tp in plan.terms if tp.term != skip
                   for i in tp.items if i.kind != "course")


def diff_plans(old: Plan, new: Plan, outcomes: dict[str, str] | None = None,
               recorded: Term | None = None) -> PlanDiff:
    """Compare two plans. With `recorded` (the semester that was just finished, from old) and its
    `outcomes`, courses from that semester are reported as done / failed instead of removed."""
    d = PlanDiff(old_last=old.terms[-1].term if old.terms else None,
                 new_last=new.terms[-1].term if new.terms else None,
                 old_hours=sum(tp.hours for tp in old.terms), new_hours=sum(tp.hours for tp in new.terms))
    before, after = _courses(old), _courses(new)
    outcomes = outcomes or {}
    for cid, (term, item) in before.items():
        name = f"{cid} {item.title}"
        if term == recorded:
            result = outcomes.get(cid, "passed")
            if result == "passed":
                d.changes.append(Change("done", name, term))
            elif cid in after:
                d.changes.append(Change("retake", name, term, after[cid][0], result))
            else:
                d.changes.append(Change("failed", name, term, detail=result))
        elif cid not in after:
            d.changes.append(Change("removed", name, term))
        elif after[cid][0] != term:
            d.changes.append(Change("moved", name, term, after[cid][0]))
        else:
            d.unchanged += 1
    for cid, (term, item) in after.items():
        if cid not in before:
            d.changes.append(Change("added", f"{cid} {item.title}", new_term=term))
    # Slots: compare counts per semester and title; a slot that moved shows as one "moved".
    old_slots, new_slots = _slots(old, skip=recorded), _slots(new)
    gone = old_slots - new_slots
    came = new_slots - old_slots
    for (term, title), n in sorted(gone.items(), key=lambda kv: kv[0][0].sort_key):
        for _ in range(n):
            target = next(((t, ti) for (t, ti) in sorted(came, key=lambda k: k[0].sort_key)
                           if ti == title and came[(t, ti)] > 0), None)
            if target:
                came[target] -= 1
                d.changes.append(Change("moved", title, term, target[0]))
            else:
                d.changes.append(Change("removed", title, term))
    for (term, title), n in sorted(came.items(), key=lambda kv: kv[0][0].sort_key):
        d.changes += [Change("added", title, new_term=term) for _ in range(max(0, n))]
    order = {"done": 0, "failed": 1, "retake": 2, "moved": 3, "added": 4, "removed": 5}
    d.changes.sort(key=lambda c: (order[c.kind], (c.new_term or c.old_term).sort_key, c.what))
    return d
