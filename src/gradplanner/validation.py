"""Checks that find course data we could not read reliably.

Each check returns a list of Issue objects. The validate_data.py script turns
them into a report so a human can fix the data by hand (in data/overrides/).

Milestone 1 note: prerequisites are only *extracted as text* so far. The
prerequisite checks here flag text that the Milestone 2 logic parser is likely
to find hard (mixed and/or, non-course conditions, etc.). Milestone 2 will add
a real "did it parse?" check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gradplanner.parser.courses import COURSE_CODE, RawCourse

# 21CS / divisional tags described on the degree-requirements page.
# Any other tag (for example "QR") is reported for human review.
KNOWN_TAGS = {"D", "POR", "SWC", "EI", "QDA"}

NEEDS_REVIEW = "NEEDS HUMAN REVIEW"


@dataclass
class Issue:
    course_id: str
    category: str     # credits | prerequisite | corequisite | offering | tags | permission
    severity: str     # error = can't use as-is; review = usable but a human should check
    message: str


def _words_left_after_removing_logic(text: str) -> str:
    """Remove course codes, bare numbers, and/or/commas; return what's left.

    If anything meaningful is left (e.g. "senior STA major"), the prerequisite
    contains a condition that is not a simple course.
    """
    leftover = re.sub(COURSE_CODE, " ", text)
    leftover = re.sub(r"\b\d{3}[A-Z]?\b", " ", leftover)
    leftover = re.sub(r"\b(and|or|either|one of|both)\b", " ", leftover, flags=re.I)
    leftover = re.sub(r"[(),;:]", " ", leftover)
    return re.sub(r"\s+", " ", leftover).strip()


def check_credits(course: RawCourse) -> list[Issue]:
    issues = [Issue(course.id, "credits", "error", p) for p in course.problems if p.startswith("credits")]
    if course.credits_min is not None and course.credits_max != course.credits_min:
        issues.append(Issue(course.id, "credits", "review",
                            f"variable credit {course.credits_min:g}-{course.credits_max:g} h; planner will use "
                            f"{course.credits_min:g} h unless the student chooses otherwise"))
    return issues


def check_prerequisite(course: RawCourse, known_ids: set[str]) -> list[Issue]:
    issues: list[Issue] = []
    text = course.prereq_raw
    if not text:
        return issues

    has_parens = "(" in text
    if re.search(r"\band\b", text) and re.search(r"\bor\b", text) and not has_parens:
        issues.append(Issue(course.id, "prerequisite", "review",
                            f"mixed 'and'/'or' without parentheses: '{text}' ({NEEDS_REVIEW}: confirm grouping)"))
    if re.search(r"\bif\b", text, re.I):
        issues.append(Issue(course.id, "prerequisite", "review",
                            f"conditional prerequisite: '{text}' ({NEEDS_REVIEW})"))
    leftover = _words_left_after_removing_logic(text)
    if leftover:
        issues.append(Issue(course.id, "prerequisite", "review",
                            f"non-course condition '{leftover}' in '{text}' ({NEEDS_REVIEW})"))

    # Course codes we don't have descriptions for (other subjects not scraped yet).
    for code in re.findall(COURSE_CODE, text):
        if code not in known_ids:
            issues.append(Issue(course.id, "prerequisite", "review",
                                f"references {code}, which is not in the scraped course data"))
    return issues


def check_corequisite(course: RawCourse, known_ids: set[str]) -> list[Issue]:
    issues: list[Issue] = []
    if course.coreq_raw:
        for code in re.findall(COURSE_CODE, course.coreq_raw):
            if code not in known_ids:
                issues.append(Issue(course.id, "corequisite", "review",
                                    f"corequisite {code} is not in the scraped course data"))
    return issues


def check_offering(course: RawCourse) -> list[Issue]:
    if course.offering_raw:
        return [Issue(course.id, "offering", "review",
                      "Bulletin text mentions timing: " + " | ".join(course.offering_raw)
                      + f" ({NEEDS_REVIEW}: convert to an offering pattern)")]
    return [Issue(course.id, "offering", "review",
                  f"no offering pattern in the Bulletin; assuming fall + spring every year ({NEEDS_REVIEW})")]


def check_tags(course: RawCourse) -> list[Issue]:
    unknown = [t for t in course.tags if t not in KNOWN_TAGS]
    if unknown:
        return [Issue(course.id, "tags", "review",
                      f"tag(s) {unknown} are not defined on the degree-requirements page ({NEEDS_REVIEW})")]
    return []


def check_permission(course: RawCourse) -> list[Issue]:
    if course.override_required:
        return [Issue(course.id, "permission", "review",
                      "requires a prerequisite override (department permission); the planner will not "
                      "schedule it automatically")]
    return []


def validate_courses(courses: list[RawCourse]) -> list[Issue]:
    """Run every check on every course and return all issues found."""
    known_ids = {c.id for c in courses}
    issues: list[Issue] = []
    seen: set[str] = set()
    for course in courses:
        if course.id in seen:
            issues.append(Issue(course.id, "duplicate", "error", "course appears more than once"))
        seen.add(course.id)
        issues += [Issue(course.id, "title", "error", p) for p in course.problems if p.startswith("title")]
        issues += check_credits(course)
        issues += check_prerequisite(course, known_ids)
        issues += check_corequisite(course, known_ids)
        issues += check_offering(course)
        issues += check_tags(course)
        issues += check_permission(course)
    return issues
