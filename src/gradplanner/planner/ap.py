"""AP exams -> Wake Forest credit, from data/overrides/ap_credit.yaml (the 2025-26 AP policy).

    apply_ap(student)   adds the credit for student.ap_exams to the profile
    ap_notes(student)   plain-English notes about that credit (conditions, scores with no credit)

AP credit becomes completed courses with source "ap" (and the policy's hours, which can be 0),
so it meets prerequisites and major/minor requirements like any finished course. It never
counts for divisional or 21st-century core requirements (policy since Fall 2007), with one
exception: see `ap_divisional_subjects`. Language exams also set the language placement.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from gradplanner import config
from gradplanner.planner.student import ApExam, CompletedCourse, OutsideCourse, StudentProfile

DONE = "done"


@dataclass
class Award:
    exam: str
    score: int
    courses: list[tuple[str, float]] = field(default_factory=list)   # (course id, hours)
    language: dict[str, str] = field(default_factory=dict)          # {"CHI": "CHI 201"} or {"SPA": "done"}
    note: str = ""


@lru_cache(maxsize=None)
def ap_table(path: Path = config.OVERRIDES_DIR / "ap_credit.yaml") -> dict:
    return yaml.safe_load(path.read_text()) or {}


def exam_names() -> list[str]:
    return sorted(ap_table()["exams"])


def ap_awards(exams: list[ApExam]) -> tuple[list[Award], list[str]]:
    """(credit awarded, notes). Unknown exams and scores below the policy's minimum earn no credit."""
    table = ap_table()
    awards, notes = [], []
    best: dict[str, int] = {}
    for e in exams:                                    # one result per exam: the highest score
        best[e.exam] = max(best.get(e.exam, 0), int(e.score))
    for exam, score in best.items():
        bands = table["exams"].get(exam)
        if bands is None:
            notes.append(f"AP {exam}: not in the Wake Forest AP policy; ask the Registrar.")
            continue
        band = next((b for b in bands if score in (b.get("scores") or [])), None)
        if band is None:
            if any(b.get("scores") for b in bands):
                notes.append(f"AP {exam} ({score}): no Wake Forest credit for this score.")
            continue
        awards.append(Award(exam, score, [(c, float(h)) for c, h in band.get("credit") or []],
                            dict(band.get("language") or {}), band.get("note", "")))
    for rule in table.get("combined") or []:
        names = rule["exams"]
        if all(n in best for n in names):
            total = sum(best[n] for n in names)
            if total >= rule["min_total"]:
                awards.append(Award(" + ".join(names), total, [(c, float(h)) for c, h in rule["credit"]]))
            else:
                notes.append(f"AP {' + '.join(names)}: combined score {total}; credit needs "
                             f"{rule['min_total']} or more.")
        elif any(n in best for n in names):
            notes.append(f"AP Economics: credit (ECN 150) needs both {' and '.join(names)} "
                         f"with a combined score of {rule['min_total']} or more.")
    notes += [f"AP {a.exam}: {a.note}" for a in awards if a.note]
    return awards, notes


def _known_courses() -> set[str] | None:
    """Catalog course ids, if the database is built (credit for other ids becomes outside credit)."""
    from gradplanner.db.store import default_db_path
    if not default_db_path().exists():
        return None
    return _catalog_ids(str(default_db_path()))


@lru_cache(maxsize=None)
def _catalog_ids(db: str) -> set[str]:
    from gradplanner.db.store import load_catalog
    return set(load_catalog(Path(db)).courses)


def _later(seq: list[str], a: str | None, b: str) -> str:
    """The later of two placement levels in a sequence ("done" is the latest)."""
    order = seq + [DONE]
    if a not in order:
        return b
    return a if order.index(a) >= order.index(b) else b


def apply_ap(student: StudentProfile, known: set[str] | None = None) -> StudentProfile:
    """Add the credit for student.ap_exams (in place; earlier AP-generated credit is replaced)."""
    student.completed = [c for c in student.completed if not c.from_exam]
    student.outside_courses = [o for o in student.outside_courses if not o.from_exam]
    if not student.ap_exams:
        return student
    known = known if known is not None else _known_courses()
    from gradplanner.planner.requirements import load_settings
    sequences = load_settings(None).languages
    have = {c.course for c in student.completed}
    awards, _ = ap_awards(student.ap_exams)
    for a in awards:
        label = f"AP {a.exam} ({a.score})"
        for cid, hours in a.courses:
            if known is not None and cid not in known:
                if hours:
                    student.outside_courses.append(OutsideCourse(f"{label}: {cid}", hours, None, "ap", a.exam))
            elif cid not in have:
                student.completed.append(CompletedCourse(cid, "ap", hours, a.exam))
                have.add(cid)
        for subj, start in a.language.items():
            if student.language in (None, "", subj):
                seq = sequences[subj].courses if subj in sequences else []
                student.language = subj
                student.language_placement = _later(seq, student.language_placement, start)
    return student


def ap_notes(student: StudentProfile) -> list[str]:
    return ap_awards(student.ap_exams)[1] if student.ap_exams else []


def ap_divisional_subjects(student: StudentProfile) -> set[str]:
    """Subjects where AP credit lets any 200/300-level course count toward the division.

    Policy: "Students who have received credit for 2 AP courses may elect to fulfill their college
    divisional with any 200 or 300 level course (excluding 390-392, 395, 398, 399)." Team reading:
    two AP-credited courses in the same department (confirm with an advisor)."""
    rule = ap_table().get("divisional_after_two") or {}
    per_subject = Counter(c.course.split()[0] for c in student.completed if c.source == "ap")
    return {s for s, n in per_subject.items() if n >= rule.get("min_courses", 2)}


def ap_divisional_ok(course_number: str, level: int) -> bool:
    rule = ap_table().get("divisional_after_two") or {}
    lo, hi = rule.get("levels", [200, 399])
    return lo <= level <= hi and course_number not in set(rule.get("excluded_numbers") or [])
