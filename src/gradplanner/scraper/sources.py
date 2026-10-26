"""The list of Bulletin pages we scrape for the MVP (CS major/minor).

Each source has a short `key` that becomes the cache file name. Adding a new
program later means adding its pages here; nothing else in the scraper changes.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    key: str    # short name, used as the cache file name
    path: str   # URL path on bulletin.wfu.edu
    kind: str   # what the parser should expect on this page


SOURCES: list[Source] = [
    # Programs
    Source("dept_csc", "/undergraduate/departments-programs/computer-science/", "department"),
    Source("prog_csc_bs", "/undergraduate/departments-programs/computer-science/bs-computer-science/", "program"),
    Source("prog_csc_ba", "/undergraduate/departments-programs/computer-science/ba-computer-science/", "program"),
    Source("prog_csc_minor", "/undergraduate/departments-programs/computer-science/minor-computer-science/", "program"),
    # Course descriptions for every subject the CS programs reference
    Source("courses_csc", "/courses-az/csc/", "courses"),
    Source("courses_mth", "/courses-az/mth/", "courses"),
    Source("courses_sta", "/courses-az/sta/", "courses"),
    # College-wide rules
    Source("degree_requirements", "/undergraduate/requirements-degrees/", "core"),
    Source("advising_registration", "/undergraduate/procedures/advising-registration/", "procedures"),
    Source("calendar", "/undergraduate/calendar/", "calendar"),
]


def sources_of_kind(kind: str) -> list[Source]:
    """Return all sources whose `kind` matches (e.g. all "courses" pages)."""
    return [s for s in SOURCES if s.kind == kind]
