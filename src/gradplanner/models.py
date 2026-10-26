"""The data model every other part of the app uses (planner, audit, recommender, UI).

`Catalog` is everything we know from one Bulletin year: courses with their
prerequisite logic, programs with requirement groups, core categories, and
degree-level rules. It is built by pipeline.py from the cached pages and
saved to / loaded from SQLite by db/store.py.

To add another program later (e.g. a Math major), add its pages to
scraper/sources.py and its id to PROGRAM_PAGES in pipeline.py. No model
changes are needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Re-export the parser dataclasses so other modules import from one place.
from gradplanner.parser.core import CoreCategory, DegreeRule
from gradplanner.parser.prereq import CNF, Atom, CourseAtom, LevelAtom, ManualAtom
from gradplanner.parser.programs import Program, ProgramExclusion, RequirementGroup, RequirementItem

__all__ = [
    "Atom", "CNF", "Catalog", "CoreCategory", "Course", "CourseAtom", "DegreeRule", "LevelAtom",
    "ManualAtom", "Offering", "Program", "ProgramExclusion", "RequirementGroup", "RequirementItem",
]


@dataclass(frozen=True)
class Offering:
    """A course runs in `term` ("fall"/"spring"/"summer") in years matching `year_parity`."""
    term: str
    year_parity: str = "any"      # any | odd | even  (calendar year of that term)
    source: str = "default"       # bulletin | override | default


@dataclass
class Course:
    id: str
    subject: str
    number: str
    title: str
    credits_min: float | None
    credits_max: float | None
    description: str
    tags: list[str]
    prereq: CNF
    prereq_raw: str | None
    prereq_status: str            # ok | resolved_by_rule | manual | failed | none | override
    coreq: CNF
    coreq_raw: str | None
    override_allowed: bool
    override_required: bool
    offerings: list[Offering]
    min_standing: str | None = None          # e.g. "senior" for "Taken during the senior year"
    exclusive_with: list[str] = field(default_factory=list)   # "credit not allowed for both"
    notes: list[str] = field(default_factory=list)            # human-readable review notes
    source_key: str = ""

    @property
    def level(self) -> int:
        """Numeric level, e.g. 201 for "CSC 201" and 105 for "MTH 105L"."""
        return int(re.match(r"\d+", self.number).group())

    @property
    def credits(self) -> float:
        """Credit hours the planner uses (the minimum for variable-credit courses)."""
        return self.credits_min if self.credits_min is not None else 0.0

    def is_offered(self, term: str, year: int) -> bool:
        """True if the course runs in `term` of calendar `year` (e.g. "fall", 2027)."""
        for o in self.offerings:
            if o.term != term:
                continue
            if o.year_parity == "any" or (o.year_parity == "odd") == (year % 2 == 1):
                return True
        return False


@dataclass
class Catalog:
    bulletin_year: str
    courses: dict[str, Course]
    programs: dict[str, Program]
    core: list[CoreCategory]
    rules: dict[str, DegreeRule]
    exclusions: list[ProgramExclusion]

    def rule(self, key: str) -> float:
        """Return a degree rule's value; raises KeyError if it wasn't found in the Bulletin."""
        return self.rules[key].value

    def excluded_from(self, program_id: str) -> dict[str, float | None]:
        """{course_id: max_hours or None} for courses restricted in this program."""
        return {e.course_id: e.max_hours for e in self.exclusions if e.program_id == program_id}
