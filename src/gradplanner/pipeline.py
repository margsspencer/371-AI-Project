"""Build a Catalog from the cached Bulletin pages plus the hand-made override files.

Order of operations:
1. Parse courses from every "courses" page.
2. Prerequisites: use data/overrides/prereq_overrides.yaml if the course is
   listed there, otherwise parse the Bulletin text.
3. Offering patterns: data/overrides/offerings.csv if listed, otherwise the
   team default (fall + spring, every year).
4. Parse programs, then map "may not count toward the major" notes to programs.
   If scripts/scrape.py --all has run, every department's courses and every
   program in its manifest are used; otherwise just the CS pages in sources.py.
5. Parse core categories and degree rules, and add team-decided categories (QR).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import yaml

from gradplanner import config
from gradplanner.models import Catalog, Course, Offering
from gradplanner.parser.calendar import parse_calendar_page
from gradplanner.parser.core import CoreCategory, parse_advising_page, parse_core_page
from gradplanner.parser.courses import COURSE_CODE, RawCourse, parse_courses_page
from gradplanner.parser.prereq import clean_prereq_text, parse_prereq
from gradplanner.parser.programs import SUBJECT_NAMES, Program, exclusions_for_course, parse_program_page
from gradplanner.scraper.discover import Page, load_manifest, subject_names
from gradplanner.scraper.fetch import PoliteFetcher
from gradplanner.scraper.sources import sources_of_kind

# program id -> cache key of its page
PROGRAM_PAGES = {"CSC-BS": "prog_csc_bs", "CSC-BA": "prog_csc_ba", "CSC-MINOR": "prog_csc_minor"}
STANDING_RE = re.compile(r"taken during the (senior|junior|sophomore) year", re.I)


class MissingCache(Exception):
    pass


def load_overrides(overrides_dir: Path = config.OVERRIDES_DIR) -> tuple[dict, dict, dict]:
    """Return (team_decisions, prereq_overrides, offering_overrides)."""
    team = yaml.safe_load((overrides_dir / "team_decisions.yaml").read_text()) or {}
    prereqs = yaml.safe_load((overrides_dir / "prereq_overrides.yaml").read_text()) or {}
    offerings: dict[str, list[Offering]] = {}
    with open(overrides_dir / "offerings.csv", newline="") as f:
        for row in csv.DictReader(f):
            source = row["source"].strip() or "override"
            offerings[row["course_id"].strip()] = [
                Offering(t.strip(), row["year_parity"].strip() or "any", source)
                for t in row["terms"].split(";") if t.strip()
            ]
    return team, prereqs, offerings


def build_course(raw: RawCourse, prereq_overrides: dict, offering_overrides: dict,
                 default_offerings: list[Offering], known_codes: set[str] | None = None) -> Course:
    """Combine a parsed RawCourse with overrides into a Course."""
    notes: list[str] = []
    override = prereq_overrides.get(raw.id)
    cleaned = clean_prereq_text(raw.prereq_raw, raw.subject, known_codes)
    if override:
        parsed = parse_prereq(override["prereq"])
        status = "override"
        notes.append(f"prerequisite from overrides file ({override.get('status', 'manual')}): "
                     f"{override.get('reason', '')}")
        if parsed.status == "manual":
            notes.append("override contains a manual condition")
    else:
        parsed = parse_prereq(cleaned.text) if raw.prereq_raw else parse_prereq(None)
        status = parsed.status
        notes += parsed.notes + [f"wording cleaned: {n}" for n in cleaned.notes]
        if cleaned.notes and status == "ok":
            status = "resolved_by_rule"
    coreq_clean = clean_prereq_text(raw.coreq_raw, raw.subject, known_codes) if raw.coreq_raw else None
    coreq_parts = [coreq_clean.text if coreq_clean else "", cleaned.coreq if not override else ""]
    coreq_parts += [coreq_clean.coreq] if coreq_clean else []
    coreq_text = ", ".join(t for t in coreq_parts if t)
    coreq = parse_prereq(coreq_text or None)
    notes += [f"corequisite: {n}" for n in coreq.notes]

    offerings = offering_overrides.get(raw.id, default_offerings)
    standing = STANDING_RE.search(raw.description)
    standing_word = standing.group(1).lower() if standing else (cleaned.standing if not override else None)
    exclusive = []
    for sentence in raw.credit_not_both:
        exclusive += [c for c in re.findall(COURSE_CODE, sentence) if c != raw.id]

    return Course(
        id=raw.id, subject=raw.subject, number=raw.number, title=raw.title,
        credits_min=raw.credits_min, credits_max=raw.credits_max, description=raw.description,
        tags=raw.tags, prereq=parsed.cnf, prereq_raw=raw.prereq_raw, prereq_status=status,
        coreq=coreq.cnf, coreq_raw=coreq_text or None,
        override_allowed=raw.override_allowed, override_required=raw.override_required,
        offerings=list(offerings), min_standing=standing_word,
        exclusive_with=exclusive, notes=notes + raw.problems, source_key=raw.source_key,
    )


SKIP_TITLE = re.compile(r"^certificate|concentration$", re.I)     # out of scope (team decision 2026-11-03)
_ID_STOP = {"with", "concentration", "in", "and", "of", "the", "minor", "b", "a", "s", "ba", "bs"}


def _program_subject(prog: Program, page_info: Page, names: dict[str, str]) -> str:
    """The department's subject code: the subject whose name matches the department page
    (e.g. womens-gender-sexuality-studies -> WGS), else the most common subject in the program."""
    dept_words = set(page_info.group.split("-")) - {"and", "of", "the", "program", "study"}
    if page_info.group == "business":
        dept_words = set()          # one page lists several business subjects (ACC, FIN, BEM)
    best, best_score = None, 0.0
    for name, code in names.items():
        words = set(re.findall(r"[a-z]+", name.replace("\u2019", ""))) - {"and", "of", "the", "s"}
        if words and dept_words:
            score = len(words & dept_words) / len(words | dept_words)
            if score > best_score:
                best, best_score = code, score
    if best and best_score >= 0.5:
        return best
    counts: dict[str, int] = {}
    for g in prog.groups:
        for it in g.items:
            for c in it.options:
                counts[c.split()[0]] = counts.get(c.split()[0], 0) + 1
    if counts:
        return max(counts, key=lambda k: (counts[k], k))
    dept = page_info.group.replace("-", " ")
    for name, code in names.items():
        if name in dept or dept in name:
            return code
    return page_info.group[:3].upper()


def _program_id(prog: Program, subject: str, taken: set[str], names: dict[str, str]) -> str:
    base = f"{subject}-{'MINOR' if prog.type == 'minor' else prog.degree or 'MAJOR'}"
    if base not in taken:
        return base
    subject_words = {w for name, code in names.items() if code == subject for w in name.split()}
    words = [w for w in re.findall(r"[a-z]+", prog.name.lower()) if w not in _ID_STOP and w not in subject_words]
    for w in words:
        candidate = f"{base}-{w.upper()}"
        if candidate not in taken:
            return candidate
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def _main_words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", title.lower().split(",")[0]) if w not in _ID_STOP}


def build_programs(pages: list[Page], page, names: dict[str, str], problems: list[str]) -> dict[str, Program]:
    """Parse every program page in the manifest (College majors/minors + School of Business).

    Ids look like CSC-BS / CSC-MINOR. When two programs would share an id, the one
    named after the subject keeps it (Mathematics, B.S. -> MTH-BS) and the other gets
    a word from its title (Applied Mathematics, B.S. -> MTH-BS-APPLIED).
    """
    parsed = []
    seen_titles: set[str] = set()
    for info in pages:
        html = page(info.key)
        first = parse_program_page(html, "TMP-X", info.key, subject_names=names)
        if SKIP_TITLE.search(first.name.strip()) or first.name in seen_titles:
            continue
        seen_titles.add(first.name)
        parsed.append((info, html, first, _program_subject(first, info, names)))

    def plain(first: Program, subject: str) -> bool:
        subject_words = {w for name, code in names.items() if code == subject for w in re.findall(r"[a-z]+", name)}
        return _main_words(first.name) <= subject_words

    # Programs named after their subject pick ids first.
    order = sorted(parsed, key=lambda x: not plain(x[2], x[3]))
    programs: dict[str, Program] = {}
    ids: dict[str, str] = {}
    for info, html, first, subject in order:
        ids[info.key] = _program_id(first, subject, set(ids.values()), names)
    for info, html, first, subject in parsed:
        pid = ids[info.key]
        prog = parse_program_page(html, pid, info.key, subject=subject, subject_names=names)
        prog.school = "Business" if info.group == "business" else "College"
        programs[pid] = prog
        problems += [f"{pid}: {p}" for p in prog.problems]
    return programs


def build_catalog(fetcher: PoliteFetcher | None = None,
                  overrides_dir: Path = config.OVERRIDES_DIR) -> tuple[Catalog, list[str]]:
    """Build the Catalog from cached pages. Returns (catalog, problems)."""
    fetcher = fetcher or PoliteFetcher()
    problems: list[str] = []

    def page(key: str) -> str:
        cached = fetcher.read_cache(key)
        if cached is None:
            raise MissingCache(f"Missing cached page '{key}'. Run: python scripts/scrape.py")
        return cached.html

    team, prereq_overrides, offering_overrides = load_overrides(overrides_dir)
    default = team.get("offering_default", {})
    default_offerings = [Offering(t, default.get("year_parity", "any"), "default")
                         for t in default.get("terms", [])]
    if not default_offerings:
        problems.append("no offering_default in team_decisions.yaml; courses will look unoffered")

    manifest = load_manifest()
    course_keys = list(dict.fromkeys([s.key for s in sources_of_kind("courses")]
                                     + [p.key for p in manifest if p.kind == "courses"]))
    raw_courses: list[RawCourse] = []
    for key in course_keys:
        raw_courses += parse_courses_page(page(key), key)
    known = {r.id for r in raw_courses}
    courses = {r.id: build_course(r, prereq_overrides, offering_overrides, default_offerings, known)
               for r in raw_courses}

    names = dict(SUBJECT_NAMES)
    names.update({name.lower(): code for code, name in subject_names(manifest).items()})
    names.update({k.lower(): v for k, v in (team.get("subject_aliases") or {}).items()})
    program_pages = [p for p in manifest if p.kind == "program"]
    if program_pages:
        programs = build_programs(program_pages, page, names, problems)
    else:
        programs = {}
        for pid, key in PROGRAM_PAGES.items():
            prog = parse_program_page(page(key), pid, key)
            programs[pid] = prog
            problems += [f"{pid}: {p}" for p in prog.problems]

    exclusions = []
    for r in raw_courses:
        found, unmatched = exclusions_for_course(r.id, r.subject, r.exclusion_notes, list(programs.values()))
        exclusions += found
        if unmatched and any(p.subject == r.subject for p in programs.values()):
            problems.append(f"{r.id}: exclusion note not understood: {unmatched}")

    core, rules, core_problems = parse_core_page(page("degree_requirements"), "degree_requirements")
    adv_rules, adv_problems = parse_advising_page(page("advising_registration"), "advising_registration")
    problems += core_problems + adv_problems
    for extra in team.get("extra_core_categories", []):
        core.append(CoreCategory(
            extra["id"], extra["family"], extra["name"], extra["courses_required"], tag=extra.get("tag"),
            placeholder=True, rules_note=extra.get("note"), source=extra.get("source", "team")))

    calendar, cal_problems = parse_calendar_page(page("calendar"))
    problems += cal_problems
    catalog = Catalog(
        bulletin_year=config.BULLETIN_YEAR, courses=courses, programs=programs, core=core,
        rules={r.key: r for r in rules + adv_rules}, exclusions=exclusions,
        subjects=subject_names(manifest), calendar=calendar,
    )
    return catalog, problems
