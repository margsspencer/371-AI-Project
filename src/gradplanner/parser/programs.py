"""Parse a program page (major/minor) into requirement groups.

The Bulletin shows requirements as one table (`table.sc_courselist`). Row types:

  header row ("Required Major Courses")         -> starts a new RequirementGroup
  course row  ("CSC 201 | Data Structures | 3")  -> a required course
  "or" row    ("or MTH 205")                     -> another option for the row above
  comment row ("Select one of the following:")   -> a choice; the indented rows under it are its options
  comment row ("Select at least 12 hours in ... at the 300-level or higher")
                                                 -> an hours rule (no list; any matching course counts)

Each group holds RequirementItems. An item is satisfied when:
  kind "course":  the student completed any one of `options` (usually just one)
  kind "choose":  completed `n` of `options` (n = 1 for "one of the following")
  kind "hours":   completed `hours` credit hours of courses matching `subject` + `min_level`
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup

from gradplanner.parser.courses import clean_text

WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10, "twelve": 12}
SUBJECT_NAMES = {"computer science": "CSC", "mathematics": "MTH", "statistics": "STA"}

HOURS_RULE_RE = re.compile(
    r"select (?:at least )?(?P<hours>\d+|\w+) hours? (?:in (?P<subjname>[a-z ]+?) courses )?"
    r"at the (?P<level>\d{3})-level or higher", re.I)
CHOOSE_RE = re.compile(r"select (?P<n>\w+) of the following", re.I)


@dataclass
class RequirementItem:
    kind: str                          # course | choose | hours | free_text
    label: str
    options: list[str] = field(default_factory=list)       # course codes
    free_text_options: list[str] = field(default_factory=list)  # options a computer can't check
    n: int = 1
    hours: float | None = None
    subject: str | None = None
    min_level: int | None = None
    needs_review: str | None = None


@dataclass
class RequirementGroup:
    label: str
    items: list[RequirementItem] = field(default_factory=list)


@dataclass
class Program:
    id: str                 # "CSC-BS"
    name: str               # "Computer Science, B.S."
    type: str               # major | minor
    degree: str | None      # BS | BA | None
    subject: str            # department subject code, "CSC"
    intro: str              # e.g. "Requires a minimum of 42 hours in computer science..."
    source_key: str
    groups: list[RequirementGroup] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _to_number(word: str) -> int | None:
    return int(word) if word.isdigit() else WORD_NUMBERS.get(word.lower())


def _row_code(row) -> str | None:
    link = row.select_one("td.codecol a.bubblelink")
    return clean_text(link.get_text()) if link else None


def _hours(row) -> str:
    cell = row.select_one("td.hourscol")
    return clean_text(cell.get_text()) if cell else ""


def _interpret_select(item: RequirementItem, text: str, hours_text: str, default_subject: str) -> None:
    """Fill in an item created from a "Select ..." comment row."""
    rule = HOURS_RULE_RE.search(text)
    if rule:
        item.kind = "hours"
        item.hours = _to_number(rule.group("hours"))
        item.min_level = int(rule.group("level"))
        subj_name = (rule.group("subjname") or "").strip().lower()
        if subj_name:
            item.subject = SUBJECT_NAMES.get(subj_name)
            if item.subject is None:
                item.needs_review = f"unknown subject name '{subj_name}'"
        else:
            item.subject = default_subject
            item.needs_review = (f"subject not stated; assumed {default_subject} (the program's department). "
                                 "NEEDS HUMAN REVIEW")
        if item.hours is None:
            item.needs_review = f"could not read hours in '{text}'"
        return
    choose = CHOOSE_RE.search(text)
    if choose:
        item.kind = "choose"
        item.n = _to_number(choose.group("n")) or 1
        return
    item.kind = "free_text"
    item.needs_review = f"unrecognized rule '{text}' (hours column: '{hours_text}'). NEEDS HUMAN REVIEW"


def parse_program_page(html: str, program_id: str, source_key: str) -> Program:
    """Parse one program page into a Program with requirement groups."""
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("h1.page-title") or soup.select_one("h1")
    name = clean_text(title_el.get_text()) if title_el else program_id
    container = soup.select_one("#requirementstextcontainer") or soup.select_one("#textcontainer") or soup
    # get_text() without a separator so drop-cap spans ("R" + "equires") rejoin into one word.
    intro_parts = [clean_text(p.get_text()) for p in container.find_all("p") if p.get_text(strip=True)]
    lowered = name.lower()
    program = Program(
        id=program_id, name=name,
        type="minor" if "minor" in lowered else "major",
        degree="BS" if "b.s." in lowered else "BA" if "b.a." in lowered else None,
        subject=program_id.split("-")[0],
        intro=" ".join(intro_parts), source_key=source_key,
    )

    table = container.select_one("table.sc_courselist")
    if table is None:
        program.problems.append("no requirements table found")
        return program

    current_group: RequirementGroup | None = None
    open_select: RequirementItem | None = None      # the "Select ..." item that indented rows attach to

    def group() -> RequirementGroup:
        nonlocal current_group
        if current_group is None:
            current_group = RequirementGroup("Requirements")
            program.groups.append(current_group)
        return current_group

    for row in table.select("tr"):
        classes = row.get("class") or []
        if "hidden" in classes:          # the hidden "Code / Title / Hours" header
            continue
        indented = row.select_one(".blockindent") is not None
        code = _row_code(row)
        comment_el = row.select_one(".courselistcomment")
        text = clean_text(row.get_text(" "))

        if "areaheader" in classes:
            current_group = RequirementGroup(clean_text(comment_el.get_text()) if comment_el else text)
            program.groups.append(current_group)
            open_select = None
        elif "orclass" in classes and code:
            items = group().items
            if not items:
                program.problems.append(f"'or' row with nothing above it: {text}")
                continue
            items[-1].options.append(code)
            items[-1].label += f" or {code}"
        elif indented and open_select is not None:
            if code:
                open_select.options.append(code)
            else:
                open_select.free_text_options.append(text)
                open_select.needs_review = (f"option '{text}' cannot be checked automatically. "
                                            "NEEDS HUMAN REVIEW")
        elif code:
            group().items.append(RequirementItem("course", code, options=[code]))
            open_select = None
        elif comment_el is not None:
            item = RequirementItem("free_text", clean_text(comment_el.get_text(" ")))
            _interpret_select(item, text, _hours(row), program.subject)
            group().items.append(item)
            open_select = item if item.kind in ("choose", "free_text") else None
        else:
            program.problems.append(f"unrecognized row: '{text}'")

    # A "choose" item must end up with options; otherwise flag it.
    for g in program.groups:
        for item in g.items:
            if item.kind == "choose" and not item.options:
                item.needs_review = "choice row has no course options. NEEDS HUMAN REVIEW"
    return program


# --- program exclusions written inside course descriptions --------------------

_EXCLUSION_PATTERNS: list[tuple[re.Pattern, str]] = [
    # (pattern, which programs: "all" = every program of the course's department, or a degree code)
    (re.compile(r"(?:may not|does not|not to) (?:be )?count(?:ed)? towards? (?:the|a)? ?computer science major or minor", re.I), "all"),
    (re.compile(r"not to be counted toward the bachelor of science in computer science", re.I), "BS"),
    (re.compile(r"not (?:to )?be counted towards? any major or minor offered (?:by|in) the department", re.I), "all"),
]
_MAX_HOURS_RE = re.compile(r"no more than (\w+) hours may be counted toward a (?:computer science )?major or minor", re.I)


@dataclass
class ProgramExclusion:
    course_id: str
    program_id: str
    max_hours: float | None     # None = may not count at all; a number = counts up to this many hours
    note: str


def exclusions_for_course(course_id: str, subject: str, notes: list[str],
                          programs: list[Program]) -> tuple[list[ProgramExclusion], list[str]]:
    """Map 'may not be counted toward ...' sentences to specific programs.

    Returns (exclusions, unmatched_notes). Only programs in the course's own
    department are affected (a MTH note doesn't change the CS major).
    """
    dept_programs = [p for p in programs if p.subject == subject]
    found: list[ProgramExclusion] = []
    unmatched: list[str] = []
    for note in notes:
        matched = False
        for pattern, scope in _EXCLUSION_PATTERNS:
            if pattern.search(note):
                matched = True
                for p in dept_programs:
                    if scope == "all" or p.degree == scope:
                        found.append(ProgramExclusion(course_id, p.id, None, note))
        max_match = _MAX_HOURS_RE.search(note)
        if max_match:
            matched = True
            hours = _to_number(max_match.group(1))
            for p in dept_programs:
                found.append(ProgramExclusion(course_id, p.id, hours, note))
        if not matched:
            unmatched.append(note)
    return found, unmatched
