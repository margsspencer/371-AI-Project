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
                "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
                "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
                "twenty-one": 21, "twenty-four": 24, "a": 1, "an": 1}
SUBJECT_NAMES = {"computer science": "CSC", "mathematics": "MTH", "statistics": "STA"}

_N = r"(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a|an)"
_H = (r"(?P<hours>\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
      r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty-one|twenty-four|twenty)")
_AT_LEAST = r"(?:at least |a minimum of |no fewer than |any |an additional |additional )?"

HOURS_RULE_RE = re.compile(
    rf"(?:select|complete) {_AT_LEAST}{_H} (?:credit )?hours? (?:of (?:coursework|electives) )?"
    r"(?:in (?P<subjname>[a-z ]+?) courses? )?(?:at|from) the (?P<level>\d{3})[- ]level or (?:higher|above)", re.I)
HOURS_LIST_RE = re.compile(
    rf"(?:select|choose|complete) {_AT_LEAST}{_H} (?:credit )?hours? (?:of |from )(?:courses )?(?:from )?"
    r"(?:the following|these|electives from)", re.I)
CHOOSE_PATTERNS = [re.compile(p, re.I) for p in (
    rf"(?:select|choose|complete|take) {_AT_LEAST}{_N}(?: additional)?(?: \w+)?(?: courses?| classes?)? "
    r"(?:of|from|among) (?:the following|these)",
    rf"^{_AT_LEAST}{_N}(?: \w+)? courses? (?:must|should|to) be (?:chosen|selected|taken) from the following",
    rf"^{_AT_LEAST}{_N} of the following",
)]


def _choose_n(text: str) -> int | None:
    """N from "Select two of the following" and similar, or None if the text isn't a choice."""
    for pat in CHOOSE_PATTERNS:
        m = pat.search(text)
        if m:
            return int(_to_number(m.group("n")) or 1)
    return None
EACH_RE = re.compile(rf"{_N}?\s*(?:course|courses|class)?\s*(?:from|in) each of the following", re.I)
SUBJECT_COUNT_RE = re.compile(
    rf"select {_AT_LEAST}{_N} (?:additional |other |more )?(?:(?P<before>[a-z ]+?) )?courses?"
    r"(?: in (?P<after>[a-z ]+?))?(?: (?:beyond|above|numbered higher than|numbered above) (?P<level>\d{3}))?"
    r"(?: at the (?P<level2>\d{3})[- ]level or (?:higher|above))?\s*\.?$", re.I)
# Rows that are information or limits, not something to complete.
NOTE_RE = re.compile(r"count towards? (?:the )?core|subject to approval|minimum grade|grade of c\b|"
                     r"^co-?requirements:?$|^pre- or co-?requisite courses:?$|"
                     r"^(?:select )?no more than|^(?:students )?may count only|^at most\b", re.I)
_LEVEL_PHRASES = [
    (re.compile(r"numbered (?:higher|greater) than (\d{3})", re.I), 1),
    (re.compile(r"above the (\d{3})[- ]level", re.I), 100),
    (re.compile(r"(?:beyond|above) (?:[A-Z]{2,4} )?(\d{3})", re.I), 1),
    (re.compile(r"(\d{3})[- ]?(?:level)?(?: or (?:higher|above))", re.I), 0),
    (re.compile(r"at or above the (\d{3})[- ]level", re.I), 0),
    (re.compile(r"at the (\d{3})(?: or \d{3})?[- ]level", re.I), 0),
    (re.compile(r"(\d{3})[- ]level", re.I), 0),
    (re.compile(r"numbered (\d{3}) or (?:above|higher)", re.I), 0),
]
# Tables that show a sample schedule or advice, not requirements.
SCHEDULE_HINT = re.compile(r"typical|sample (?:schedule|plan)|suggested (?:schedule|plan|sequence)|"
                           r"well[- ]positioned|four[- ]year (?:plan|schedule)|plan of study", re.I)


@dataclass
class RequirementItem:
    kind: str                          # course | choose | hours | free_text | note
    label: str
    options: list[str] = field(default_factory=list)       # course codes
    free_text_options: list[str] = field(default_factory=list)  # options a computer can't check
    n: int = 1
    hours: float | None = None
    subject: str | None = None
    min_level: int | None = None
    needs_review: str | None = None
    # kind "hours" with `options`: hours must come from that list (subject/min_level unused)
    # `subject` may name several subjects joined by "|" ("MTH|STA")
    # kind "note": information or a limit the planner doesn't enforce (doesn't block support)


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
    school: str = "College"  # College | Business

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def support(self) -> str:
        """full: every requirement can be checked by the planner; partial: some need an advisor;
        none: no requirements could be read."""
        items = [it for g in self.groups for it in g.items]
        if not items:
            return "none"
        if any(it.kind == "free_text" or (it.kind == "choose" and len(it.options) < it.n) for it in items):
            return "partial"
        if all(it.kind == "note" for it in items):
            return "none"
        return "full"


def _to_number(word: str | None) -> float | None:
    if word is None:
        return None
    word = word.lower()
    try:
        return float(word) if "." in word else int(word)
    except ValueError:
        return WORD_NUMBERS.get(word)


def _code(text: str) -> str:
    """Cross-listed codes: "ENG/AES 357" -> "ENG 357"; "EDU/AES 310/AAS 326" -> "EDU 310"."""
    text = clean_text(text)
    m = re.match(r"([A-Z]{2,4})(?:/[A-Z]{2,4})*\s+(\d{3}[A-Z]?)", text)
    return f"{m.group(1)} {m.group(2)}" if m else text


def _row_code(row) -> str | None:
    link = row.select_one("td.codecol a.bubblelink")
    return _code(link.get_text()) if link else None


def _row_codes(row) -> list[str]:
    """All codes in a row's code cell: "BIO 150 & 150L" -> ["BIO 150", "BIO 150L"]."""
    cell = row.select_one("td.codecol")
    if cell is None:
        return []
    codes = []
    for a in cell.select("a.bubblelink"):
        code = _code(a.get_text())
        if re.fullmatch(r"\d{3}[A-Z]?", code) and codes:      # "& 150L" -> same subject as before
            code = f"{codes[-1].split()[0]} {code}"
        codes.append(code)
    if len(codes) == 1:
        # "& 150L" written as text after the link
        subj = codes[0].split()[0]
        codes += [f"{subj} {m}" for m in re.findall(r"&\s*(\d{3}[A-Z]?)\b", cell.get_text(" "))
                  if f"{subj} {m}" not in codes]
    return codes


def _hours(row) -> str:
    cell = row.select_one("td.hourscol")
    return clean_text(cell.get_text()) if cell else ""


def _subject_from_name(phrase: str | None, subject_names: dict[str, str]) -> str | None:
    if not phrase:
        return None
    phrase = phrase.strip().lower()
    if phrase in subject_names:
        return subject_names[phrase]
    if phrase.upper() in subject_names.values():
        return phrase.upper()
    return None


def _generic_rule(item: RequirementItem, text: str, default_subject: str, names: dict[str, str]) -> bool:
    """Last try before free text: "N hours/courses" + subject(s) + optional level.

    Handles rows like "Select 24 hours in Greek", "Choose 3 additional electives in STA at the
    200 or 300 level", "Select four other MTH or STA courses of at least 3 hours each",
    "Select 6 hours of any other 300-level courses in the department". Each one is flagged
    so a person can confirm the reading.
    """
    low = text.lower()
    # Rows with extra conditions we can't capture stay free text (an advisor checks them).
    if re.search(r"\bexcept|excluding|one of (?:which|these)|no more than|at least \w+ (?:\w+ )?(?:of which|must)|"
                 r"including at least|\(at least|each of the following|\bmust\b|\bstudents\b|one or two|"
                 r"^or\b|\bor (?:one|two|three)\b|may include|up to|related to|offered (?:by|at)|"
                 r"the humanities|the sciences", low) \
            or text.rstrip().endswith(":") or item.label.rstrip().endswith(":"):
        return False
    small = r"(?P<n>\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a|an)"
    hours_m = re.search(r"(?P<hours>\d{1,2}(?:\.\d)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
                        r"(?:\s*-\s*[\d.]+)? (?:credit |elective credit |additional |additional credit )?hours?\b(?! each)",
                        text, re.I)
    count_m = re.search(rf"\b{small} (?:additional |other |more |upper-level )?(?:\d{{3}}-level )?(?:(?:\d|\w+)-hour )?"
                        r"(?:[A-Z]{2,4} (?:or [A-Z]{2,4} )?)?(?:[A-Za-z]+ )?(?:courses?|electives?|classes)\b", text, re.I)
    codes = [c for c in re.findall(r"\b([A-Z]{2,4})\b", text) if c in names.values()]
    subjects = list(dict.fromkeys(codes))
    if not subjects:
        rest = low
        for name in sorted(names, key=len, reverse=True):     # longest names first ("art history" before "history")
            if re.search(rf"\b{re.escape(name)}\b", rest):
                subjects.append(names[name])
                rest = re.sub(rf"\b{re.escape(name)}\b", " ", rest)
        subjects = list(dict.fromkeys(subjects))
    if not subjects and re.search(r"in the department|in the major", low):
        subjects = [default_subject]
    if not subjects:
        return False
    if hours_m:
        hours = _to_number(hours_m.group("hours"))
    elif count_m:
        n = _to_number(count_m.group("n"))
        per = 4 if re.search(r"\b(?:four|4)-hour", text, re.I) else 3
        hours = per * n if n else None
    else:
        return False
    if not hours:
        return False
    level = 0
    for pat, bump in _LEVEL_PHRASES:
        m = pat.search(text)
        if m:
            level = int(m.group(1)) + bump
            break
    item.kind, item.hours, item.subject, item.min_level = "hours", float(hours), "|".join(subjects), level
    item.needs_review = (f"read as {hours:g} hours of {' or '.join(subjects)} courses"
                         + (f" numbered {level}+" if level else "") + ". Confirm.")
    return True


def _interpret_select(item: RequirementItem, text: str, hours_text: str, default_subject: str,
                      subject_names: dict[str, str] | None = None) -> None:
    """Fill in an item created from a "Select ..." comment row."""
    names = subject_names or SUBJECT_NAMES
    if NOTE_RE.search(text):
        item.kind = "note"
        item.needs_review = ("limit not enforced by the planner; check with your advisor"
                             if re.search(r"no more than|may count only|at most", text, re.I) else None)
        return
    rule = HOURS_RULE_RE.search(text)
    if rule:
        item.kind = "hours"
        item.hours = _to_number(rule.group("hours"))
        item.min_level = int(rule.group("level"))
        subj_name = (rule.group("subjname") or "").strip().lower()
        if subj_name:
            item.subject = _subject_from_name(subj_name, names)
            if item.subject is None:
                item.kind = "free_text"
                item.needs_review = f"unknown subject name '{subj_name}'. NEEDS HUMAN REVIEW"
        else:
            item.subject = default_subject
            item.needs_review = (f"subject not stated; assumed {default_subject} (the program's department). "
                                 "NEEDS HUMAN REVIEW")
        if item.hours is None:
            item.kind = "free_text"
            item.needs_review = f"could not read hours in '{text}'"
        return
    hours_list = HOURS_LIST_RE.search(text)
    if hours_list:
        item.kind = "hours"          # options are filled in from the indented rows below
        item.hours = _to_number(hours_list.group("hours"))
        return
    n = _choose_n(text)
    if n is not None:
        item.kind, item.n = "choose", n
        return
    count = SUBJECT_COUNT_RE.search(text)
    if count:
        subj = (_subject_from_name(count.group("after"), names) or _subject_from_name(count.group("before"), names))
        if subj:
            n = int(_to_number(count.group("n")) or 1)
            item.kind, item.subject, item.hours = "hours", subj, 3 * n
            level = count.group("level") or count.group("level2")
            item.min_level = int(level) + (1 if count.group("level") else 0) if level else 0
            item.needs_review = f"'{n} course(s)' read as {3 * n} hours of {subj}. Check 4-hour courses."
            return
    if _generic_rule(item, text, default_subject, names):
        return
    item.kind = "free_text"
    item.needs_review = f"unrecognized rule '{text}' (hours column: '{hours_text}'). NEEDS HUMAN REVIEW"


def _starts_rule(text: str) -> bool:
    """True if a row looks like a new requirement ("Select ...", "Choose ...") rather than an option."""
    return bool(re.match(r"\s*(?:select|choose|complete|take|students|at least|a minimum)\b", text, re.I))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _heading_before(table) -> str:
    prev = table.find_previous(["h2", "h3", "h4", "h5", "p"])
    return clean_text(prev.get_text(" ")) if prev else ""


def _program_type(name: str) -> tuple[str, str | None]:
    lowered = name.lower()
    degree = ("BS" if re.search(r"\bb\.?\s?s\.?\b", lowered) else "BA" if re.search(r"\bb\.?\s?a\.?\b", lowered)
              else None)
    return ("minor" if "minor" in lowered else "major"), degree


def parse_program_page(html: str, program_id: str, source_key: str, subject: str | None = None,
                       subject_names: dict[str, str] | None = None) -> Program:
    """Parse one program page into a Program with requirement groups.

    `subject` is the department's subject code (default: the part of the id
    before "-"). `subject_names` maps lowercase names to codes
    ({"computer science": "CSC"}) for rules like "12 hours in computer science courses".

    A page can have several requirement tables. Tables that show a sample
    schedule are skipped. A table whose heading names a group mentioned in
    another table ("One course from each of the following groups: I. Ancient
    Greek Philosophy, ...") becomes that group's list of options.
    """
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("h1.page-title") or soup.select_one("h1")
    name = clean_text(title_el.get_text()) if title_el else program_id
    container = soup.select_one("#requirementstextcontainer") or soup.select_one("#textcontainer") or soup
    # get_text() without a separator so drop-cap spans ("R" + "equires") rejoin into one word.
    intro_parts = [clean_text(p.get_text()) for p in container.find_all("p") if p.get_text(strip=True)]
    ptype, degree = _program_type(name)
    program = Program(id=program_id, name=name, type=ptype, degree=degree,
                      subject=subject or program_id.split("-")[0], intro=" ".join(intro_parts),
                      source_key=source_key)

    tables = container.select("table.sc_courselist")
    if not tables:
        program.problems.append("no requirements table found (requirements are written as text)")
        return program

    # Group-name tables: heading -> course codes
    named: dict[str, list[str]] = {}
    for t in tables:
        heading = _norm(_heading_before(t))
        if heading and len(heading) < 80:
            named.setdefault(heading, [c for row in t.select("tr") for c in _row_codes(row)])

    # Rows can link to a list further down the page ("Choose three of the following" -> #anchor).
    linked: dict[str, object] = {}
    for a in container.select("table.sc_courselist a[href*='#']"):
        anchor = a["href"].split("#", 1)[1]
        target = soup.find(id=anchor) or soup.find(attrs={"name": anchor})
        table = target.find_next("table", class_="sc_courselist") if target else None
        if table is not None and table not in a.parents:
            linked[anchor] = table

    per_table: list[tuple[str, list[RequirementGroup]]] = []
    for t in tables:
        if any(t is lt for lt in linked.values()):
            continue                       # a list that a row links to; read as that row's options
        heading = _heading_before(t)
        if SCHEDULE_HINT.search(heading) or any(
                _norm(r.get_text(" ")) in ("first year", "sophomore year", "junior year", "senior year")
                for r in t.select("tr.areaheader")):
            continue
        per_table.append((_norm(heading), _parse_table(t, program, subject_names, named, linked)))

    referenced = {_norm(o) for _, groups in per_table for g in groups for it in g.items
                  for o in it.free_text_options}
    for _, groups in per_table:
        for g in groups:
            for it in g.items:
                if it.kind == "free_text" and not it.options and not it.free_text_options:
                    h = _named_in(it.label, named)
                    if h:
                        referenced.add(h)
    for heading, groups in per_table:
        if heading in referenced:
            continue                       # this table is a list of options for a group, used below
        program.groups.extend(groups)
    _resolve_group_references(program, named)
    _approved_lists(program)

    for g in program.groups:
        for item in g.items:
            if item.kind == "free_text" and item.options and not item.free_text_options:
                _choice_from_label(item)
            if item.kind == "choose" and not item.options:
                item.needs_review = "choice row has no course options. NEEDS HUMAN REVIEW"
                item.kind = "free_text"
            if item.kind == "hours" and item.subject is None and not item.options:
                item.needs_review = "hours rule with no course list. NEEDS HUMAN REVIEW"
                item.kind = "free_text"
    program.groups = [g for g in program.groups if g.items]
    return program


def _parse_table(table, program: Program, subject_names, named: dict[str, list[str]],
                 linked: dict | None = None) -> list[RequirementGroup]:
    groups: list[RequirementGroup] = []
    current: RequirementGroup | None = None
    open_select: RequirementItem | None = None      # the "Select ..." item that indented rows attach to

    def group() -> RequirementGroup:
        nonlocal current
        if current is None:
            current = RequirementGroup("Requirements")
            groups.append(current)
        return current

    for row in table.select("tr"):
        classes = row.get("class") or []
        if "hidden" in classes or "listsum" in classes:     # header row / "Total Hours" row
            continue
        indented = row.select_one(".blockindent") is not None
        codes = _row_codes(row)
        comment_el = row.select_one(".courselistcomment")
        text = clean_text(row.get_text(" "))

        if "areaheader" in classes:
            label = clean_text(comment_el.get_text()) if comment_el else text
            sel = RequirementItem("free_text", label)
            _interpret_select(sel, label, _hours(row), program.subject, subject_names)
            current = RequirementGroup(label)
            groups.append(current)
            open_select = None
            if sel.kind in ("choose", "hours") and not sel.needs_review:
                # A header like "Select 4 courses from:" starts a choice for the rows below it.
                current.items.append(sel)
                open_select = sel
        elif "orclass" in classes and codes:
            items = group().items
            target = open_select if open_select is not None and open_select.options else (items[-1] if items else None)
            if target is None:
                program.problems.append(f"'or' row with nothing above it: {text}")
                continue
            if target.kind == "course" and len(codes) > 1:
                target.kind = "free_text"
                target.needs_review = f"'or' between course bundles: {target.label} or {text}. NEEDS HUMAN REVIEW"
            target.options += [c for c in codes[:1] if c not in target.options]
            target.label += f" or {codes[0]}"
        elif open_select is not None and (indented or (codes and open_select.kind in ("choose", "hours"))
                                          or (not codes and _norm(text) in named)
                                          or (not codes and open_select.kind in ("choose", "hours")
                                              and not open_select.options and not _starts_rule(text))):
            if codes:
                open_select.options += [c for c in codes if c not in open_select.options]
            else:
                open_select.free_text_options.append(text)
                open_select.needs_review = (f"option '{text}' cannot be checked automatically. "
                                            "NEEDS HUMAN REVIEW")
        elif codes:
            if len(codes) > 1:
                for c in codes:          # "BIO 150 & 150L": both required
                    group().items.append(RequirementItem("course", c, options=[c]))
            else:
                group().items.append(RequirementItem("course", codes[0], options=[codes[0]]))
            open_select = None
        elif comment_el is not None:
            item = RequirementItem("free_text", clean_text(comment_el.get_text(" ")))
            _interpret_select(item, text, _hours(row), program.subject, subject_names)
            link = comment_el.select_one("a[href*='#']")
            target = (linked or {}).get(link["href"].split("#", 1)[1]) if link else None
            if target is not None:
                for r in target.select("tr"):
                    if "hidden" in (r.get("class") or []) or "listsum" in (r.get("class") or []):
                        continue
                    rc = _row_codes(r)
                    if rc:
                        item.options += [c for c in rc[:1] if c not in item.options]
                    elif clean_text(r.get_text(" ")):
                        item.free_text_options.append(clean_text(r.get_text(" ")))
                if item.kind == "free_text":
                    _choice_from_label(item)
                if item.free_text_options and item.kind in ("choose", "hours"):
                    item.needs_review = f"some options can't be checked automatically: {item.free_text_options[0][:80]}"
            group().items.append(item)
            open_select = item if item.kind in ("choose", "free_text") or (
                item.kind == "hours" and item.subject is None) else None
        else:
            program.problems.append(f"unrecognized row: '{text}'")
    return groups


def _named_in(label: str, named: dict[str, list[str]]) -> str | None:
    """The longest table heading that appears inside `label` ("Select one course from Methods Courses")."""
    low = _norm(label)
    hits = [h for h in named if len(h) >= 8 and named[h] and re.search(rf"\b{re.escape(h)}\b", low)]
    return max(hits, key=len) if hits else None


def _choice_from_label(item: RequirementItem) -> None:
    """A free-text row with a course list under it: "Select three electives in Japanese film:" -> choose 3;
    "Select three hours in pre-modern history:" -> 3 hours from the list."""
    m = re.match(rf"(?:select|choose|complete|take)?\s*{_AT_LEAST}{_H}\s+(?:credit )?hours?\b", item.label, re.I)
    if m:
        item.kind, item.hours, item.needs_review = "hours", float(_to_number(m.group("hours"))), None
        return
    m = re.match(rf"(?:select|choose|complete|take)?\s*{_AT_LEAST}{_N}\b(?!\s*(?:-|hour))", item.label, re.I)
    if m:
        item.kind, item.n, item.needs_review = "choose", int(_to_number(m.group("n")) or 1), None


LIST_MIN = 12          # an unheaded table with at least this many courses is a list of approved electives
TOTAL_RE = re.compile(rf"(?:requires|minimum of|total of|consists of|must complete)\s+(?:a minimum of |at least )?{_H}\s+"
                      r"(?:credit )?hours", re.I)


def _approved_lists(program: Program) -> None:
    """Long course tables without a "Select ..." header are lists of approved electives, not
    requirements ("The minor requires 18 hours chosen from the following"). Groups titled
    "Recommended ..." are advice, not requirements."""
    all_items = [it for g in program.groups for it in g.items]
    total_m = TOTAL_RE.search(program.intro)
    total = float(_to_number(total_m.group("hours"))) if total_m else None
    for g in program.groups:
        if re.search(r"recommend", g.label, re.I):
            for it in g.items:
                it.kind, it.needs_review = "note", None
                it.label = f"Recommended: {it.label}"
            continue
        courses = [it for it in g.items if it.kind == "course"]
        if g.label != "Requirements" or len(courses) < LIST_MIN or len(courses) != len(g.items):
            continue
        codes = [c for it in courses for c in it.options]
        if len(courses) == len(all_items) and total:
            g.items = [RequirementItem("hours", f"{total:g} hours from the approved course list", options=codes,
                                       hours=total, needs_review="course table read as a list of approved "
                                       "electives; check other rules on the page (e.g. different departments)")]
        else:
            g.items = [RequirementItem("free_text", f"Electives from the approved course list ({len(codes)} courses)",
                                       options=codes, needs_review="how many to take isn't stated in the table. "
                                       "NEEDS HUMAN REVIEW")]


def _resolve_group_references(program: Program, named: dict[str, list[str]]) -> None:
    """Replace option names that are headings of other tables with those tables' courses."""
    for g in program.groups:
        new_items = []
        for item in g.items:
            if item.kind == "free_text" and not item.options and not item.free_text_options:
                h = _named_in(item.label, named)
                if h:
                    item.options = list(named[h])
                    _choice_from_label(item)
                    if item.kind == "free_text":
                        item.kind, item.n, item.needs_review = "choose", 1, None
                    new_items.append(item)
                    continue
            refs = [o for o in item.free_text_options if named.get(_norm(o))]
            if not refs:
                new_items.append(item)
                continue
            rest = [o for o in item.free_text_options if o not in refs]
            each = EACH_RE.search(item.label)
            if each and not rest:
                n = int(_to_number(each.group("n")) or 1)
                for ref in refs:
                    new_items.append(RequirementItem("choose", f"{ref} ({n} course{'s' if n > 1 else ''})",
                                                     options=list(named[_norm(ref)]), n=n))
                continue
            for ref in refs:
                item.options += [c for c in named[_norm(ref)] if c not in item.options]
            item.free_text_options = rest
            if not rest:
                item.needs_review = None
                if item.kind == "free_text":
                    item.kind, item.n = "choose", _choose_n(item.label) or 1
            new_items.append(item)
        g.items = new_items


# --- program exclusions written inside course descriptions --------------------

_EXCLUSION_PATTERNS: list[tuple[re.Pattern, str]] = [
    # (pattern, which programs: "all" = every program of the course's department, or a degree code)
    (re.compile(r"(?:may not|does not|do not|cannot|will not|not to) (?:be )?count(?:ed)? (?:towards?|for) "
                r"(?:the |a |any )?(?:[\w\u2019'&,-]+ ){0,6}?(?:major|minor)", re.I), "all"),
    (re.compile(r"not to be counted toward the bachelor of science in computer science", re.I), "BS"),
    (re.compile(r"not (?:to )?be counted towards? any major or minor offered (?:by|in) the department", re.I), "all"),
]
_MAX_HOURS_RE = re.compile(r"no more than (\w+) (?:credit )?hours (?:[^.]*? )?may (?:be )?count(?:ed)? towards? "
                           r"(?:the |a |any )?(?:[\w ]+ )?(?:major|minor)", re.I)


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
