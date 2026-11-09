"""A student profile: what they've done and what they want the plan to look like.

Profiles are local YAML files with FAKE data only (see samples/). They never
go into the database. Example:

    name: Sam Example (fictional)
    program: CSC-BS                # or, for several:  majors: [CSC-BS, MTH-BA]  minors: [STA-MINOR]
    entry_term: Fall 2026          # first semester at Wake Forest
    next_term: Fall 2026           # first semester to plan
    graduation_term: Spring 2030   # last semester (hard deadline)
    max_hours_per_term: 17
    completed:
      - {course: CSC 111, source: ap}
    in_progress: [CSC 112]         # enrolled this semester; planning assumes they pass
    outside_courses:               # courses not in our catalog (other departments)
      - {name: HST 101, hours: 3, counts_for: DIV-I}
    interests: [CSC 374]           # electives the student definitely wants
    career: machine-learning-engineer   # pick-list id from recommender/careers.yaml (optional)
    career_text: "AI for healthcare"     # the student's own words (optional)
    abroad:                        # study-abroad semesters (optional)
      - {term: Spring 2029, program: wfu, location: "Worrell House, London"}   # wfu | affiliate
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
    counts_for: str | None = None   # a core category id like "DIV-I", a major/minor rule key like
                                    # "FIN-BS:1.0" (credit from abroad), or None for a plain elective
    source: str = "wfu"             # wfu | ap | transfer | affiliate (non-Wake Forest study abroad)


@dataclass
class AbroadTerm:
    """A semester abroad. 'wfu' = a Wake Forest program (Wake Forest credit);
    'affiliate' = an approved non-Wake Forest program (transfer credit)."""
    term: Term
    program: str = "wfu"
    location: str = ""

    def __post_init__(self) -> None:
        if self.program not in ("wfu", "affiliate"):
            raise ValueError(f"study abroad program must be 'wfu' or 'affiliate', got {self.program!r}")


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
    in_progress: list[str] = field(default_factory=list)   # current semester, before next_term
    outside_courses: list[OutsideCourse] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)
    career: str | None = None
    career_text: str = ""
    other_programs: list[str] = field(default_factory=list)   # second major and/or minors
    abroad: list[AbroadTerm] = field(default_factory=list)

    def abroad_in(self, term: Term) -> AbroadTerm | None:
        return next((a for a in self.abroad if a.term == term), None)

    @property
    def programs(self) -> list[str]:
        """Every major and minor, the main one first."""
        return [self.program] + [p for p in self.other_programs if p != self.program]

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
    def finished(self) -> list[CompletedCourse]:
        """Completed plus in-progress courses. The planner assumes in-progress courses are passed."""
        done = {c.course for c in self.completed}
        return self.completed + [CompletedCourse(c) for c in self.in_progress if c not in done]

    @property
    def completed_ids(self) -> set[str]:
        """Ids of completed and in-progress courses (what the planner treats as done)."""
        return {c.course for c in self.finished}


def profile_from_dict(d: dict) -> StudentProfile:
    programs = list(d.get("programs") or [])         # all majors and minors, main major first
    if not programs:
        programs = list(d.get("majors") or ([d["program"]] if d.get("program") else [])) + list(d.get("minors") or [])
    if not programs:
        raise ValueError("a profile needs 'program' (or 'majors' / 'minors')")
    p = StudentProfile(
        name=d["name"],
        program=programs[0],
        other_programs=programs[1:],
        entry_term=Term.parse(d["entry_term"]),
        next_term=Term.parse(d["next_term"]),
        graduation_term=Term.parse(d["graduation_term"]),
        max_hours_per_term=d.get("max_hours_per_term", 17),
        include_summer=d.get("include_summer", False),
        summer_max_hours=d.get("summer_max_hours", 8),
        language_courses_needed=d.get("language_courses_needed", 1),
        completed=[CompletedCourse(**c) for c in d.get("completed", [])],
        in_progress=list(d.get("in_progress", [])),
        outside_courses=[OutsideCourse(**c) for c in d.get("outside_courses", [])],
        interests=list(d.get("interests", [])),
        career=d.get("career"),
        career_text=d.get("career_text", ""),
        abroad=[AbroadTerm(Term.parse(a["term"]), a.get("program", "wfu"), a.get("location", ""))
                for a in d.get("abroad") or []],
    )
    if p.graduation_term < p.next_term:
        raise ValueError("graduation_term is before next_term")
    if p.next_term < p.entry_term:
        raise ValueError("next_term is before entry_term")
    return p


def load_profile(path: str | Path) -> StudentProfile:
    return profile_from_dict(yaml.safe_load(Path(path).read_text()))


def profile_to_dict(p: StudentProfile) -> dict:
    """The YAML form of a profile (what load_profile reads)."""
    d = {"name": p.name}
    if len(p.programs) == 1:
        d["program"] = p.program
    else:
        d["programs"] = p.programs                       # main major first, then second major / minors
    d.update({
        "entry_term": str(p.entry_term), "next_term": str(p.next_term), "graduation_term": str(p.graduation_term),
        "max_hours_per_term": p.max_hours_per_term, "include_summer": p.include_summer,
        "summer_max_hours": p.summer_max_hours, "language_courses_needed": p.language_courses_needed,
        "completed": [{"course": c.course, "source": c.source} for c in p.completed],
        "in_progress": list(p.in_progress),
        "outside_courses": [{"name": o.name, "hours": o.hours, "counts_for": o.counts_for, "source": o.source}
                            for o in p.outside_courses],
        "interests": list(p.interests), "career": p.career, "career_text": p.career_text,
        "abroad": [{"term": str(a.term), "program": a.program, "location": a.location} for a in p.abroad],
    })
    return d


def save_profile(p: StudentProfile, path: str | Path) -> None:
    Path(path).write_text("# FICTIONAL student (saved by the planner)\n" + yaml.safe_dump(profile_to_dict(p), sort_keys=False))
