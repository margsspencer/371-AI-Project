"""What the planner returns: semesters with courses and slots, plus what each counts toward."""

from __future__ import annotations

from dataclasses import dataclass, field

from gradplanner.planner.terms import Term


@dataclass
class PlanItem:
    id: str                 # "CSC 201", "DIV-I", or "ELECTIVE"
    title: str
    hours: float
    kind: str               # course | core_slot | elective_slot
    counts_toward: list[str] = field(default_factory=list)   # requirement keys (see Plan.labels)


@dataclass
class TermPlan:
    term: Term
    items: list[PlanItem] = field(default_factory=list)

    @property
    def hours(self) -> float:
        return sum(i.hours for i in self.items)

    @property
    def course_ids(self) -> list[str]:
        return [i.id for i in self.items if i.kind == "course"]


@dataclass
class Plan:
    student_name: str
    program_id: str
    status: str                       # optimal | feasible | infeasible | unknown
    terms: list[TermPlan] = field(default_factory=list)
    # What already-completed courses count toward: {"CSC 111": ["CSC-BS:0.0", "DIV-V"]}
    completed_counts: dict[str, list[str]] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)       # requirement key -> readable label
    explanations: list[str] = field(default_factory=list)     # why there is no plan
    notes: list[str] = field(default_factory=list)            # things the student should check

    @property
    def ok(self) -> bool:
        return self.status in ("optimal", "feasible")

    def term_of(self, course_id: str) -> Term | None:
        for tp in self.terms:
            if course_id in tp.course_ids:
                return tp.term
        return None
