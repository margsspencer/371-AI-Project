"""Parse the college-wide rules: Core Curriculum categories and degree-level numbers.

Sources:
- /undergraduate/requirements-degrees/          basic, divisional, 21CS requirements; 120 hours
- /undergraduate/procedures/advising-registration/  normal/maximum load; class standing

Every number we use (120 total hours, 17-hour maximum, ...) is pulled out
with a pattern AND stored with the sentence it came from. If a pattern stops
matching (e.g. the Bulletin wording changes next year), the rule goes
missing and validation reports it. We never fall back to a remembered value.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup

from gradplanner.parser.courses import clean_text
from gradplanner.parser.prereq import CNF, CourseAtom, cnf_to_text, parse_prereq, to_cnf


@dataclass
class CoreCategory:
    id: str                       # "BASIC-FYS", "DIV-I", "21CS-QDA", "QR"
    family: str                   # basic | divisional | 21CS | divisional-wide
    name: str
    courses_required: int
    tag: str | None = None        # course tag that satisfies it, e.g. "D", "QDA"
    departments: str | None = None
    course_cnf: CNF = field(default_factory=list)   # for basic reqs met by specific courses
    timing: str | None = None     # e.g. "year1", "by_end_of_year2"
    placeholder: bool = False     # True = planner reserves a generic slot (no specific course)
    rules_note: str | None = None
    source: str = "bulletin"
    needs_review: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["course_cnf_text"] = cnf_to_text(self.course_cnf)
        return d


@dataclass
class DegreeRule:
    key: str
    value: float
    source_key: str
    source_text: str


def _section_after(container, heading_text: str, tags=("h2",)):
    """Return the heading element whose text starts with `heading_text`."""
    for h in container.find_all(tags):
        if clean_text(h.get_text()).lower().startswith(heading_text.lower()):
            return h
    return None


def _timing(text: str) -> str | None:
    t = text.lower()
    if "first year" in t:
        return "year1"
    if "end of the second year" in t or "before the end of the second year" in t:
        return "by_end_of_year2"
    return None


def _basic_category(li_text: str, hes_text: str) -> CoreCategory:
    """Turn one 'Basic Requirements' bullet into a category."""
    text = clean_text(li_text)
    codes = re.findall(r"[A-Z]{3} \d{3}", text)
    # "WRI 109 (2 h) and WRI 110 (2 h), or WRI 111 (4 h)" -> (WRI 109 and WRI 110) or WRI 111
    pair_or_single = re.search(r"([A-Z]{3} \d{3}) \(\d h\) and ([A-Z]{3} \d{3}) \(\d h\),? or ([A-Z]{3} \d{3})", text)
    if pair_or_single:
        a, b, c = pair_or_single.groups()
        cnf = to_cnf(("or", [("and", [CourseAtom(a), CourseAtom(b)]), CourseAtom(c)]))
        return CoreCategory("BASIC-" + a.split()[0], "basic", "Writing", 1, course_cnf=cnf,
                            timing=_timing(text), rules_note=text)
    if "foreign language" in text.lower():
        level = re.search(r"(\d{3})-level", text)
        return CoreCategory("BASIC-LANG", "basic", "Foreign language (one 200-level course)", 1,
                            placeholder=True, rules_note=text,
                            needs_review=("number of language courses before the 200 level depends on the "
                                          "student's placement; the student enters it" if level else
                                          "could not read the language level. NEEDS HUMAN REVIEW"))
    if len(codes) == 1:
        code = codes[0]
        timing = _timing(text) or (_timing(hes_text) if code.startswith("HES") else None)
        name = {"FYS": "First-year seminar", "HES": "Health and exercise science"}.get(code[:3], code)
        return CoreCategory("BASIC-" + code.split()[0], "basic", name, 1,
                            course_cnf=parse_prereq(code).cnf, timing=timing, rules_note=text)
    return CoreCategory("BASIC-UNKNOWN", "basic", text[:60], 1, placeholder=True, rules_note=text,
                        needs_review="could not interpret this basic requirement. NEEDS HUMAN REVIEW")


def parse_core_page(html: str, source_key: str) -> tuple[list[CoreCategory], list[DegreeRule], list[str]]:
    """Parse /requirements-degrees/. Returns (categories, rules, problems)."""
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#textcontainer") or soup
    full_text = clean_text(container.get_text(" "))
    categories: list[CoreCategory] = []
    rules: list[DegreeRule] = []
    problems: list[str] = []

    # --- degree-level numbers ---
    m = re.search(r"[^.]*for a total of (\d+) credit hours[^.]*\.", full_text)
    if m:
        rules.append(DegreeRule("total_hours", float(m.group(1)), source_key, m.group(0).strip()))
    else:
        problems.append("total credit hours sentence not found")
    m = re.search(r"[^.]*only courses of (\d+) or more semester hours count towards satisfying basic and divisional[^.]*\.", full_text)
    if m:
        rules.append(DegreeRule("min_hours_for_core_course", float(m.group(1)), source_key, m.group(0).strip()))
    m = re.search(r"[^.]*at most, (\d+) hours of credit from sources other than Wake Forest[^.]*\.", full_text)
    if m:
        rules.append(DegreeRule("max_outside_hours", float(m.group(1)), source_key, m.group(0).strip()))

    # --- basic requirements: the first <ul> after the "Basic Requirements" heading ---
    hes_heading = _section_after(container, "Requirement in Health and Exercise Science")
    hes_text = clean_text(hes_heading.find_next("p").get_text(" ")) if hes_heading else ""
    basic_h = _section_after(container, "Basic Requirements")
    basic_ul = basic_h.find_next("ul") if basic_h else None
    if basic_ul is None:
        problems.append("basic requirements list not found")
    else:
        for li in basic_ul.find_all("li", recursive=False):
            categories.append(_basic_category(li.get_text(" "), hes_text))

    # --- divisional table ---
    div_h = _section_after(container, "Divisional Requirements")
    table = div_h.find_next("table") if div_h else None
    restrictions_h = _section_after(container, "Special Restrictions")
    restrictions = ""
    if restrictions_h and restrictions_h.find_next("ul"):
        restrictions = " ".join(clean_text(li.get_text(" "))
                                for li in restrictions_h.find_next("ul").find_all("li"))
    if table is None:
        problems.append("divisional requirements table not found")
    else:
        for tr in table.select("tr"):
            cells = [clean_text(td.get_text(" ")) for td in tr.find_all("td")]
            if len(cells) != 3 or not cells[2].isdigit():
                continue
            division, departments, count = cells
            roman = re.match(r"([IVX]+)[.:]", division)
            categories.append(CoreCategory(
                f"DIV-{roman.group(1) if roman else division}", "divisional", division, int(count),
                tag="D", departments=departments, placeholder=True, rules_note=restrictions or None))

    # --- 21CS: every <h3> like "Ethical Inquiry (EI)" in that section ---
    cs21_h = _section_after(container, "Additional Requirements: 21")
    if cs21_h is None:
        problems.append("21CS section not found")
    else:
        for h3 in cs21_h.find_all_next("h3"):
            abbr = re.search(r"\(([A-Z]{2,4})\)\s*$", clean_text(h3.get_text()))
            if abbr:
                categories.append(CoreCategory(
                    f"21CS-{abbr.group(1)}", "21CS", clean_text(h3.get_text()), 1, tag=abbr.group(1),
                    placeholder=True,
                    rules_note="Cannot be satisfied by AP, IB, or transfer credit; a course may carry "
                               "several 21CS tags but counts for only one."))
    return categories, rules, problems


def parse_advising_page(html: str, source_key: str) -> tuple[list[DegreeRule], list[str]]:
    """Pull load limits and class-standing thresholds from the advising page."""
    soup = BeautifulSoup(html, "html.parser")
    text = clean_text((soup.select_one("#textcontainer") or soup).get_text(" "))
    rules: list[DegreeRule] = []
    problems: list[str] = []
    patterns = {
        "max_hours_per_semester": r"[^.]*maximum credit hours allowed[^.]*without permission is (\d+)[^.]*\.",
        "normal_hours_per_semester": r"[^.]*normal load for a full-time undergraduate student is (\d+) credit hours[^.]*\.",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text)
        if m:
            rules.append(DegreeRule(key, float(m.group(1)), source_key, m.group(0).strip()))
        else:
            problems.append(f"rule '{key}' not found on advising page")
    for standing in ("Sophomore", "Junior", "Senior"):
        m = re.search(rf"{standing}\s*[—-]\s*completion of no fewer than (\d+) hours", text)
        if m:
            rules.append(DegreeRule(f"standing_{standing.lower()}_hours", float(m.group(1)), source_key, m.group(0)))
        else:
            problems.append(f"class standing threshold for {standing} not found")
    return rules, problems
