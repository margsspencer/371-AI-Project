"""Turn Bulletin course pages into simple course records.

A course on the Bulletin looks like this (HTML simplified):

    <div class="courseblock">
      <p class="courseblocktitle"><strong>CSC 112.  Fundamentals of Computer Science.  (4 h)</strong></p>
      <p class="courseblockdesc">A study of ... P-CSC 111 or prerequisite override required. (D)</p>
    </div>

This module pulls out the pieces *as text*: title, credits, the prerequisite
sentence ("P-..."), the corequisite sentence ("C-..."), tags like (D) or
(QDA), and any sentence that talks about when the course is offered.
Turning the prerequisite text into AND/OR logic happens later, in prereq.py.
Anything we can't read is recorded in `problems` so the validation report
can show it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup

# "CSC 112", "MTH 105L", "BIO 150L"
COURSE_CODE = r"[A-Z]{2,4} \d{3}[A-Z]?"
TITLE_RE = re.compile(rf"^(?P<code>{COURSE_CODE})\.\s+(?P<rest>.+)$")
# "(4 h)", "(1-3 h)", "(1.5 h)", "(0 h)"
CREDITS_RE = re.compile(r"\((?P<lo>\d+(?:\.\d+)?)(?:\s*[-–]\s*(?P<hi>\d+(?:\.\d+)?))?\s*h\)\s*$")
# One or more trailing tags like "(D)" or "(QDA)" at the very end of a description.
TRAILING_TAG_RE = re.compile(r"\(\s*([A-Z]{1,5}(?:\s*,\s*[A-Z]{1,5})*)\s*\)\s*$")
# Start of a prerequisite ("P-", "P -", "P- ") or corequisite ("C-") sentence.
PREREQ_START_RE = re.compile(r"(?<![A-Za-z])P\s*-\s*")
COREQ_START_RE = re.compile(r"(?<![A-Za-z])C\s*-\s*")
OVERRIDE_RE = re.compile(r",?\s*or\s+(?:the\s+)?prerequisite override required", re.I)
# Sentences about *when* a course runs. "offered by the department" is not one of them.
OFFERING_RE = re.compile(
    r"\b(offered\s+(?:in\s+|during\s+)?(?:the\s+)?(?:fall|spring|summer|every|each|alternate|only|odd|even)"
    r"|fall semester|spring semester|in the fall|in the spring|alternate years"
    r"|odd[- ]numbered years|even[- ]numbered years|summer session)\b",
    re.I,
)
# "... and prerequisite override required" = permission is ALWAYS needed (not just an alternative).
OVERRIDE_REQUIRED_RE = re.compile(r",?\s*and\s+(?:the\s+)?prerequisite override required", re.I)
EXCLUSION_RE = re.compile(r"\b(not (?:to )?be counted|may not be counted|no more than .* may be counted)\b", re.I)
CREDIT_NOT_BOTH_RE = re.compile(r"credit not allowed for both", re.I)


@dataclass
class RawCourse:
    """One course as read from the Bulletin, before any logic parsing."""

    id: str                         # "CSC 201"
    subject: str                    # "CSC"
    number: str                     # "201"
    title: str
    credits_min: float | None
    credits_max: float | None
    description: str                # full description text as shown on the Bulletin
    prereq_raw: str | None          # e.g. "CSC 112 and MTH 117" (override clause removed)
    coreq_raw: str | None           # e.g. "MTH 111"
    override_allowed: bool          # "or prerequisite override required" was present
    override_required: bool         # "and prerequisite override required" / "P-Prerequisite override required"
    tags: list[str]                 # e.g. ["D", "QDA"]
    offering_raw: list[str]         # sentences that mention fall/spring/offered
    exclusion_notes: list[str]      # "May not be counted towards the CS major..." sentences
    credit_not_both: list[str]      # "Credit not allowed for both X and Y" sentences
    linked_codes: list[str]         # every course code that is a link in the description
    source_key: str                 # which cached page it came from
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def clean_text(text: str) -> str:
    """Replace non-breaking spaces and squeeze repeated whitespace."""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def split_sentences(text: str) -> list[str]:
    """Split on periods that end a sentence, ignoring periods inside parentheses.

    We track how deep we are inside "( ... )" so text like
    "(also requires BIO 150, 150L, and BIO 160L if taken as BIO 387)" stays whole.
    """
    sentences, current, depth = [], [], 0
    for i, ch in enumerate(text):
        current.append(ch)
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "." and depth == 0:
            next_char = text[i + 1] if i + 1 < len(text) else " "
            if next_char.isspace():
                sentences.append("".join(current).strip())
                current = []
    tail = "".join(current).strip()
    if tail:
        sentences.append(tail)
    return sentences


def extract_requisite(sentences: list[str], start_re: re.Pattern) -> tuple[str | None, str | None]:
    """Find the sentence that starts with "P-" (or "C-") and return (body, whole sentence)."""
    for sentence in sentences:
        match = start_re.search(sentence)
        # Only accept the marker at the start of the sentence, e.g. "P-CSC 111 ...".
        if match and match.start() == 0:
            body = sentence[match.end():].rstrip(". ").strip()
            return body, sentence
    return None, None


def parse_title(title_text: str) -> tuple[str | None, str | None, float | None, float | None, list[str]]:
    """Split "CSC 112.  Fundamentals of Computer Science.  (4 h)" into its parts."""
    problems: list[str] = []
    text = clean_text(title_text)
    credits_min = credits_max = None
    credit_match = CREDITS_RE.search(text)
    if credit_match:
        credits_min = float(credit_match.group("lo"))
        credits_max = float(credit_match.group("hi") or credit_match.group("lo"))
        text = text[: credit_match.start()].strip()
    else:
        problems.append(f"credits: could not read credit hours from title '{title_text.strip()}'")

    title_match = TITLE_RE.match(text)
    if not title_match:
        problems.append(f"title: could not read course code from '{text}'")
        return None, None, credits_min, credits_max, problems
    title = title_match.group("rest").strip().rstrip(".").strip()
    return title_match.group("code"), title, credits_min, credits_max, problems


def parse_course_block(block, source_key: str) -> RawCourse | None:
    """Parse one <div class="courseblock"> element. Returns None if it has no title."""
    title_el = block.select_one(".courseblocktitle")
    desc_el = block.select_one(".courseblockdesc")
    if title_el is None:
        return None

    code, title, cmin, cmax, problems = parse_title(title_el.get_text(" "))
    if code is None:
        code = "UNKNOWN"
    subject, number = code.split(" ", 1) if " " in code else (code, "")

    description = clean_text(desc_el.get_text(" ")) if desc_el else ""
    # The link text is the course code; links make code-finding reliable.
    linked_codes = [clean_text(a.get_text()) for a in (desc_el.select("a.bubblelink") if desc_el else [])]
    # bs4 inserts a space before punctuation that followed a link ("CSC 111 ."), so tidy that.
    description = re.sub(r"\s+([.,;)])", r"\1", description)
    description = re.sub(r"\(\s+", "(", description)

    # Peel off trailing tags such as "(D)" or "(QDA)" (there may be more than one).
    tags: list[str] = []
    body = description
    while True:
        tag_match = TRAILING_TAG_RE.search(body)
        if not tag_match:
            break
        tags = [t.strip() for t in tag_match.group(1).split(",")] + tags
        body = body[: tag_match.start()].rstrip()

    sentences = split_sentences(body)
    prereq_body, _ = extract_requisite(sentences, PREREQ_START_RE)
    coreq_body, _ = extract_requisite(sentences, COREQ_START_RE)

    override_allowed = False
    override_required = False
    if prereq_body is not None:
        if OVERRIDE_REQUIRED_RE.search(prereq_body):
            override_required = True
            prereq_body = OVERRIDE_REQUIRED_RE.sub("", prereq_body).strip(" ,;")
        if OVERRIDE_RE.search(prereq_body):
            override_allowed = True
            prereq_body = OVERRIDE_RE.sub("", prereq_body).strip(" ,;")
        # "P-Prerequisite override required." means no course logic at all.
        if re.fullmatch(r"(?i)prerequisite override required\.?", prereq_body):
            override_required = True
            prereq_body = ""

    offering_raw = [s for s in sentences if OFFERING_RE.search(s)]
    exclusion_notes = [s for s in sentences if EXCLUSION_RE.search(s)]
    credit_not_both = [s for s in sentences if CREDIT_NOT_BOTH_RE.search(s)]

    if desc_el is None:
        problems.append("description: missing")

    return RawCourse(
        id=code,
        subject=subject,
        number=number,
        title=title or "",
        credits_min=cmin,
        credits_max=cmax,
        description=description,
        prereq_raw=prereq_body,
        coreq_raw=coreq_body,
        override_allowed=override_allowed,
        override_required=override_required,
        tags=tags,
        offering_raw=offering_raw,
        exclusion_notes=exclusion_notes,
        credit_not_both=credit_not_both,
        linked_codes=linked_codes,
        source_key=source_key,
        problems=problems,
    )


def parse_courses_page(html: str, source_key: str) -> list[RawCourse]:
    """Parse every course block on a Courses A-Z (or department) page."""
    soup = BeautifulSoup(html, "html.parser")
    courses = []
    for block in soup.select("div.courseblock"):
        course = parse_course_block(block, source_key)
        if course is not None:
            courses.append(course)
    return courses
