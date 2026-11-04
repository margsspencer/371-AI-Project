"""Find every department, program, and course page from the Bulletin's index pages.

Instead of a hand-typed list, we read three index pages:
  /undergraduate/departments-programs/                 -> one link per department
  /undergraduate/school-business/programs-majors/      -> School of Business programs
  /courses-az/                                         -> one link per course subject

Each department page links to its program pages (bs-biology/, minor-biology/, ...).
The result is saved as manifest.json next to the cached pages, so building the
database later works offline.

Scope (team decision 2026-11-03): College majors and minors plus School of
Business programs. Certificates and self-designed/interdisciplinary programs
are left out.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from bs4 import BeautifulSoup

from gradplanner import config
from gradplanner.scraper.fetch import PoliteFetcher

DEPT_INDEX = "/undergraduate/departments-programs/"
BUSINESS_INDEX = "/undergraduate/school-business/programs-majors/"
COURSE_INDEX = "/courses-az/"

SKIP_DEPARTMENTS = {"interdisciplinary-major", "interdisciplinary-honors"}   # self-designed (out of scope)
SKIP_PROGRAM_PREFIXES = ("cert-",)                                             # certificates (out of scope)

MANIFEST = config.RAW_DIR / "manifest.json"


@dataclass
class Page:
    key: str        # cache file name
    path: str       # URL path
    kind: str       # department | program | courses
    group: str = "" # department slug (programs) or subject code (courses)
    label: str = "" # link text from the index page


def _links(html: str, prefix: str) -> list[tuple[str, str]]:
    """(href, text) for links directly under `prefix` (one level deeper), without PDFs or anchors."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#")[0]
        rest = href[len(prefix):] if href.startswith(prefix) else ""
        if not rest or rest.endswith(".pdf") or rest.count("/") != 1 or href in seen:
            continue
        seen.add(href)
        out.append((href, a.get_text(" ", strip=True)))
    return out


def _slug(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def discover(fetcher: PoliteFetcher, log=print) -> list[Page]:
    pages: list[Page] = []

    # Course subjects: link text looks like "Computer Science (CSC)"
    html = fetcher.fetch("index_courses", COURSE_INDEX).html
    for href, text in _links(html, COURSE_INDEX):
        subject = _slug(href).upper()
        pages.append(Page(f"courses_{subject.lower()}", href, "courses", subject, text))

    # Departments and their programs
    html = fetcher.fetch("index_departments", DEPT_INDEX).html
    departments = [(h, t) for h, t in _links(html, DEPT_INDEX) if _slug(h) not in SKIP_DEPARTMENTS]
    departments.append((BUSINESS_INDEX, "School of Business"))
    for href, text in departments:
        dept = "business" if href == BUSINESS_INDEX else _slug(href)
        key = f"dept_{dept.replace('-', '_')}"
        try:
            dept_html = fetcher.fetch(key, href).html
        except Exception as exc:                                    # one bad page shouldn't stop the run
            log(f"  could not fetch {href}: {exc}")
            continue
        pages.append(Page(key, href, "department", dept, text))
        for phref, ptext in _links(dept_html, href):
            pslug = _slug(phref)
            if pslug.startswith(SKIP_PROGRAM_PREFIXES):
                continue
            pages.append(Page(f"prog_{dept.replace('-', '_')}__{pslug.replace('-', '_')}", phref, "program",
                              dept, ptext))
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps([asdict(p) for p in pages], indent=1))
    return pages


def load_manifest() -> list[Page]:
    """Pages found by the last discovery run (empty if discovery hasn't run)."""
    if not MANIFEST.exists():
        return []
    return [Page(**d) for d in json.loads(MANIFEST.read_text())]


def subject_names(manifest: list[Page]) -> dict[str, str]:
    """{'CSC': 'Computer Science', ...} from the Courses A-Z link texts."""
    out = {}
    for p in manifest:
        if p.kind == "courses":
            m = re.match(r"(.+?)\s*\(([A-Z]{2,4})\)\s*$", p.label)
            out[p.group] = m.group(1).strip() if m else p.label
    return out
