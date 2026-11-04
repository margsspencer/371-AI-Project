"""A student profile: what they've done and what they want the plan to look like.

Profiles are local YAML files with FAKE data only (see samples/). They never
go into the database. Example:

    name: Sam Example (fictional)
    program: CSC-BS
    entry_term: Fall 2026          # first semester at Wake Forest
    next_term: Fall 2026           # first semester to plan
    graduation_term: Spring 2030   # last semester (hard deadline)
    max_hours_per_term: 17
    completed:
      - {course: CSC 111, source: ap}
    outside_courses:               # courses not in our catalog (other departments)
      - {name: HST 101, hours: 3, counts_for: DIV-I}
    interests: [CSC 374]           # electives to prefer (later: the recommender fills this)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from gradplanner.planner.terms import Term, terms_between


@dataclass
class CompletedCourse:
    course: str                  # catalog id, e.g. "CSC 111"
    source: str = "wfu"          # wfu | ap | transfer  (AP/transfer can't count for divisional or 21CS)


@dataclass
class OutsideCourse:
    """A finished course from a department we don't scrape, e.g. a history class."""
    name: str
    hours: float
    counts_for: str | None = None   # a core category id like "DIV-I", or None for a plain elective
    source: str = "wfu"


@dataclass
class StudentProfile:
    name: str
    program: str
    entry_term: Term
    next_term: Term
    graduation_term: Term
    max_hours_per_term: float = 17
    include_summer: bool = False
    summer_max_hours: float = 8
    language_courses_needed: int = 1    # language courses still needed to finish one 200-level course
    completed: list[CompletedCourse] = field(default_factory=list)
    outside_courses: list[OutsideCourse] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)

    def plan_terms(self) -> list[Term]:
        return terms_between(self.next_term, self.graduation_term, self.include_summer)

    def max_hours(self, term: Term, bulletin_max: float) -> float:
        """Hour limit for a term: the student's own limit, never above the Bulletin maximum."""
        own = self.summer_max_hours if term.season == "summer" else self.max_hours_per_term
        return min(own, bulletin_max)

    def year_index(self, term: Term) -> int:
        """0 for the student's first school year, 1 for the second, ..."""
        return term.academic_year - self.entry_term.academic_year

    @property
    def completed_ids(self) -> set[str]:
        return {c.course for c in self.completed}


def profile_from_dict(d: dict) -> StudentProfile:
    p = StudentProfile(
        name=d["name"],
        program=d["program"],
        entry_term=Term.parse(d["entry_term"]),
        next_term=Term.parse(d["next_term"]),
        graduation_term=Term.parse(d["graduation_term"]),
        max_hours_per_term=d.get("max_hours_per_term", 17),
        include_summer=d.get("include_summer", False),
        summer_max_hours=d.get("summer_max_hours", 8),
        language_courses_needed=d.get("language_courses_needed", 1),
        completed=[CompletedCourse(**c) for c in d.get("completed", [])],
        outside_courses=[OutsideCourse(**c) for c in d.get("outside_courses", [])],
        interests=list(d.get("interests", [])),
    )
    if p.graduation_term < p.next_term:
        raise ValueError("graduation_term is before next_term")
    if p.next_term < p.entry_term:
        raise ValueError("next_term is before entry_term")
    return p


def load_profile(path: str | Path) -> StudentProfile:
    return profile_from_dict(yaml.safe_load(Path(path).read_text()))
