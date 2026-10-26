"""Build a Catalog from the cached Bulletin pages plus the hand-made override files.

Order of operations:
1. Parse courses from every "courses" page.
2. Prerequisites: use data/overrides/prereq_overrides.yaml if the course is
   listed there, otherwise parse the Bulletin text.
3. Offering patterns: data/overrides/offerings.csv if listed, otherwise the
   team default (fall + spring, every year).
4. Parse programs, then map "may not count toward the major" notes to programs.
5. Parse core categories and degree rules, and add team-decided categories (QR).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import yaml

from gradplanner import config
from gradplanner.models import Catalog, Course, Offering
from gradplanner.parser.core import CoreCategory, parse_advising_page, parse_core_page
from gradplanner.parser.courses import COURSE_CODE, RawCourse, parse_courses_page
from gradplanner.parser.prereq import parse_prereq
from gradplanner.parser.programs import exclusions_for_course, parse_program_page
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
                 default_offerings: list[Offering]) -> Course:
    """Combine a parsed RawCourse with overrides into a Course."""
    notes: list[str] = []
    override = prereq_overrides.get(raw.id)
    if override:
        parsed = parse_prereq(override["prereq"])
        status = "override"
        notes.append(f"prerequisite from overrides file ({override.get('status', 'manual')}): "
                     f"{override.get('reason', '')}")
        if parsed.status == "manual":
            notes.append("override contains a manual condition")
    else:
        parsed = parse_prereq(raw.prereq_raw)
        status = parsed.status
        notes += parsed.notes
    coreq = parse_prereq(raw.coreq_raw)
    notes += [f"corequisite: {n}" for n in coreq.notes]

    offerings = offering_overrides.get(raw.id, default_offerings)
    standing = STANDING_RE.search(raw.description)
    exclusive = []
    for sentence in raw.credit_not_both:
        exclusive += [c for c in re.findall(COURSE_CODE, sentence) if c != raw.id]

    return Course(
        id=raw.id, subject=raw.subject, number=raw.number, title=raw.title,
        credits_min=raw.credits_min, credits_max=raw.credits_max, description=raw.description,
        tags=raw.tags, prereq=parsed.cnf, prereq_raw=raw.prereq_raw, prereq_status=status,
        coreq=coreq.cnf, coreq_raw=raw.coreq_raw,
        override_allowed=raw.override_allowed, override_required=raw.override_required,
        offerings=list(offerings), min_standing=standing.group(1).lower() if standing else None,
        exclusive_with=exclusive, notes=notes + raw.problems, source_key=raw.source_key,
    )


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

    raw_courses: list[RawCourse] = []
    for source in sources_of_kind("courses"):
        raw_courses += parse_courses_page(page(source.key), source.key)
    courses = {r.id: build_course(r, prereq_overrides, offering_overrides, default_offerings)
               for r in raw_courses}

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

    catalog = Catalog(
        bulletin_year=config.BULLETIN_YEAR, courses=courses, programs=programs, core=core,
        rules={r.key: r for r in rules + adv_rules}, exclusions=exclusions,
    )
    return catalog, problems
