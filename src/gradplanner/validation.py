"""Checks that find data we could not read reliably, plus the report writer.

Severity levels:
  error  : the data can't be used as-is (planner results would be wrong). Fix first.
  review : usable, but a human should confirm (marked NEEDS HUMAN REVIEW).
  info   : handled by a team rule or override; listed so the paper can cite it.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from gradplanner.models import Catalog, CourseAtom, ManualAtom
from gradplanner.parser.prereq import cnf_to_text

NEEDS_REVIEW = "NEEDS HUMAN REVIEW"
# Rules that the planner needs; missing any of them is an error.
REQUIRED_RULES = ["total_hours", "max_hours_per_semester", "standing_senior_hours"]


@dataclass
class Issue:
    subject_id: str   # course id, program id, or core category id
    category: str
    severity: str     # error | review | info
    message: str


def check_courses(catalog: Catalog) -> list[Issue]:
    issues: list[Issue] = []
    tag_names = {k.tag for k in catalog.core if k.tag}
    for c in catalog.courses.values():
        # credits
        if c.credits_min is None:
            issues.append(Issue(c.id, "credits", "error", "credit hours could not be read"))
        elif c.credits_max != c.credits_min:
            issues.append(Issue(c.id, "credits", "info",
                                f"variable credit {c.credits_min:g}-{c.credits_max:g} h; planner uses "
                                f"{c.credits_min:g} h"))
        # prerequisites
        logic = cnf_to_text(c.prereq)
        if c.prereq_status == "failed":
            issues.append(Issue(c.id, "prerequisite", "error", f"could not parse '{c.prereq_raw}'"))
        elif c.prereq_status == "manual":
            manual = [str(a) for cl in c.prereq for a in cl if isinstance(a, ManualAtom)]
            issues.append(Issue(c.id, "prerequisite", "review",
                                f"'{c.prereq_raw}' -> {logic}; manual condition(s) {manual} ({NEEDS_REVIEW})"))
        elif c.prereq_status == "override":
            issues.append(Issue(c.id, "prerequisite", "review",
                                f"from overrides file: {logic} (Bulletin: '{c.prereq_raw}') ({NEEDS_REVIEW})"))
        elif c.prereq_status == "resolved_by_rule":
            issues.append(Issue(c.id, "prerequisite", "info",
                                f"'{c.prereq_raw}' -> {logic} (team and/or rule)"))
        for clause in c.prereq + c.coreq:
            for atom in clause:
                if isinstance(atom, CourseAtom) and atom.code not in catalog.courses:
                    issues.append(Issue(c.id, "prerequisite", "review",
                                        f"references {atom.code}, which is not in the scraped data"))
        if c.coreq:
            issues.append(Issue(c.id, "corequisite", "info", f"corequisite: {cnf_to_text(c.coreq)}"))
        # offerings: the default is team-approved, so only non-default ones are listed
        if not c.offerings:
            issues.append(Issue(c.id, "offering", "error", "no offering pattern at all"))
        elif any(o.source != "default" for o in c.offerings):
            terms = ", ".join(f"{o.term} ({o.year_parity}, {o.source})" for o in c.offerings)
            issues.append(Issue(c.id, "offering", "info", f"offered: {terms}"))
        # tags
        unknown = [t for t in c.tags if t not in tag_names]
        if unknown:
            issues.append(Issue(c.id, "tags", "review", f"tag(s) {unknown} not defined ({NEEDS_REVIEW})"))
        if c.override_required:
            issues.append(Issue(c.id, "permission", "review",
                                "needs department permission; not auto-scheduled"))
    return issues


_MIN_HOURS_RE = re.compile(r"minimum of (\d+) hours in ([a-z ]+?) and", re.I)


def check_program_hour_total(catalog: Catalog, program) -> Issue | None:
    """Cross-check: required department hours in the table should add up to the
    "Requires a minimum of N hours in ..." sentence on the same page."""
    m = _MIN_HOURS_RE.search(program.intro)
    if not m:
        return Issue(program.id, "program", "review", "no 'minimum of N hours' sentence to cross-check")
    stated = float(m.group(1))
    total = 0.0
    for g in program.groups:
        for it in g.items:
            if it.kind == "course" and it.options and it.options[0].startswith(program.subject + " "):
                course = catalog.courses.get(it.options[0])
                total += course.credits if course else 0
            elif it.kind == "hours" and it.subject == program.subject:
                total += it.hours or 0
    if total != stated:
        return Issue(program.id, "program", "error",
                     f"requirement table adds up to {total:g} {program.subject} hours but the page says {stated:g}")
    return Issue(program.id, "program", "info", f"table adds up to the stated {stated:g} {program.subject} hours")


def check_programs(catalog: Catalog) -> list[Issue]:
    issues: list[Issue] = []
    for p in catalog.programs.values():
        total_issue = check_program_hour_total(catalog, p)
        if total_issue:
            issues.append(total_issue)
        if not p.groups:
            issues.append(Issue(p.id, "program", "error", "no requirements parsed"))
        for g in p.groups:
            for it in g.items:
                if it.needs_review:
                    issues.append(Issue(p.id, "program", "review", f"[{g.label}] {it.label}: {it.needs_review}"))
                for code in it.options:
                    if code not in catalog.courses:
                        issues.append(Issue(p.id, "program", "error",
                                            f"[{g.label}] requires {code}, which is not in the course data"))
    return issues


def check_core(catalog: Catalog) -> list[Issue]:
    issues: list[Issue] = []
    for k in catalog.core:
        if k.needs_review:
            issues.append(Issue(k.id, "core", "review", k.needs_review))
        if k.source != "bulletin":
            issues.append(Issue(k.id, "core", "info", f"not from the Bulletin; source: {k.source}"))
    for key in REQUIRED_RULES:
        if key not in catalog.rules:
            issues.append(Issue(key, "rules", "error", f"degree rule '{key}' was not found in the Bulletin"))
    return issues


def validate_catalog(catalog: Catalog, pipeline_problems: list[str] | None = None) -> list[Issue]:
    issues = [Issue("pipeline", "pipeline", "error", p) for p in (pipeline_problems or [])]
    return issues + check_courses(catalog) + check_programs(catalog) + check_core(catalog)


def summary_lines(catalog: Catalog, issues: list[Issue]) -> list[str]:
    """Short console summary."""
    status = Counter(c.prereq_status for c in catalog.courses.values())
    default_offer = sum(1 for c in catalog.courses.values() if all(o.source == "default" for o in c.offerings))
    lines = [
        f"Courses: {len(catalog.courses)}   Programs: {', '.join(catalog.programs)}   "
        f"Core categories: {len(catalog.core)}   Degree rules: {len(catalog.rules)}",
        "Prerequisite parse status: " + ", ".join(f"{k}={v}" for k, v in sorted(status.items())),
        f"Offering pattern: {default_offer} courses use the team default (fall + spring)",
    ]
    sev = Counter(i.severity for i in issues)
    lines.append(f"Issues: error={sev.get('error', 0)}  review={sev.get('review', 0)}  info={sev.get('info', 0)}")
    return lines


def report_markdown(catalog: Catalog, issues: list[Issue]) -> str:
    """Full report, grouped by severity then category."""
    lines = [f"# Data validation report: Bulletin {catalog.bulletin_year}", ""]
    lines += [f"- {s}" for s in summary_lines(catalog, issues)] + [""]
    lines += ["## Degree rules found (with source sentence)", ""]
    for r in catalog.rules.values():
        lines.append(f"- `{r.key}` = **{r.value:g}**: \"{r.source_text}\"")
    lines.append("")
    for severity in ("error", "review", "info"):
        group = [i for i in issues if i.severity == severity]
        lines += [f"## {severity.upper()} ({len(group)})", ""]
        by_cat: dict[str, list[Issue]] = defaultdict(list)
        for i in group:
            by_cat[i.category].append(i)
        for cat, items in by_cat.items():
            lines += [f"### {cat} ({len(items)})", ""]
            lines += [f"- **{i.subject_id}**: {i.message}" for i in items]
            lines.append("")
    return "\n".join(lines)
