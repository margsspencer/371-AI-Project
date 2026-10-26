"""Save a Catalog to SQLite and load it back.

We rebuild the whole database file each time (it's small, ~100 KB), which
keeps this code simple: no migrations, no partial updates.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from gradplanner import config
from gradplanner.models import (
    CNF, Catalog, CoreCategory, Course, CourseAtom, DegreeRule, LevelAtom, ManualAtom, Offering,
    Program, ProgramExclusion, RequirementGroup, RequirementItem,
)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def default_db_path(year: str = config.BULLETIN_YEAR) -> Path:
    return config.DATA_DIR / f"bulletin_{year}.db"


# --- atoms <-> (kind, value) -------------------------------------------------

def atom_to_row(atom) -> tuple[str, str]:
    if isinstance(atom, CourseAtom):
        return "course", atom.code
    if isinstance(atom, LevelAtom):
        return "level", f"{atom.subject}:{atom.min_level}"
    return "manual", atom.text


def row_to_atom(kind: str, value: str):
    if kind == "course":
        return CourseAtom(value)
    if kind == "level":
        subject, level = value.split(":")
        return LevelAtom(subject, int(level))
    return ManualAtom(value)


def cnf_to_json(cnf: CNF) -> str:
    return json.dumps([[atom_to_row(a) for a in clause] for clause in cnf])


def cnf_from_json(text: str) -> CNF:
    return [[row_to_atom(k, v) for k, v in clause] for clause in json.loads(text)]


def _insert_cnf(cur: sqlite3.Cursor, table: str, course_id: str, cnf: CNF) -> None:
    for ci, clause in enumerate(cnf):
        for oi, atom in enumerate(clause):
            kind, value = atom_to_row(atom)
            cur.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?)", (course_id, ci, oi, kind, value))


def _load_cnf(cur: sqlite3.Cursor, table: str) -> dict[str, CNF]:
    result: dict[str, CNF] = {}
    rows = cur.execute(f"SELECT course_id, clause_no, kind, value FROM {table} "
                       "ORDER BY course_id, clause_no, option_no").fetchall()
    for course_id, clause_no, kind, value in rows:
        clauses = result.setdefault(course_id, [])
        while len(clauses) <= clause_no:
            clauses.append([])
        clauses[clause_no].append(row_to_atom(kind, value))
    return result


# --- save ---------------------------------------------------------------------

def save_catalog(catalog: Catalog, path: Path | None = None) -> Path:
    """Write the catalog to a fresh SQLite file and return its path."""
    path = path or default_db_path(catalog.bulletin_year)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.executescript(SCHEMA_PATH.read_text())
    year = catalog.bulletin_year
    cur.execute("INSERT INTO bulletin VALUES (?,?,?)",
                (year, datetime.now(timezone.utc).isoformat(timespec="seconds"), config.BULLETIN_BASE_URL))

    for c in catalog.courses.values():
        cur.execute("INSERT INTO course VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            c.id, year, c.subject, c.number, c.title, c.credits_min, c.credits_max, c.description,
            json.dumps(c.tags), c.prereq_raw, c.prereq_status, c.coreq_raw, int(c.override_allowed),
            int(c.override_required), c.min_standing, json.dumps(c.notes), c.source_key))
        _insert_cnf(cur, "prerequisite", c.id, c.prereq)
        _insert_cnf(cur, "corequisite", c.id, c.coreq)
        for o in c.offerings:
            cur.execute("INSERT INTO offering_pattern VALUES (?,?,?,?)", (c.id, o.term, o.year_parity, o.source))
        for other in c.exclusive_with:
            cur.execute("INSERT OR IGNORE INTO exclusive_pair VALUES (?,?)", tuple(sorted((c.id, other))))

    for p in catalog.programs.values():
        cur.execute("INSERT INTO program VALUES (?,?,?,?,?,?,?,?)",
                    (p.id, year, p.name, p.type, p.degree, p.subject, p.intro, p.source_key))
        for gi, g in enumerate(p.groups):
            cur.execute("INSERT INTO requirement_group (program_id, label, sort_order) VALUES (?,?,?)",
                        (p.id, g.label, gi))
            group_id = cur.lastrowid
            for ii, it in enumerate(g.items):
                cur.execute("INSERT INTO requirement_item (group_id, kind, label, n, hours, subject, "
                            "min_level, needs_review, sort_order) VALUES (?,?,?,?,?,?,?,?,?)",
                            (group_id, it.kind, it.label, it.n, it.hours, it.subject, it.min_level,
                             it.needs_review, ii))
                item_id = cur.lastrowid
                for oi, code in enumerate(it.options):
                    cur.execute("INSERT INTO requirement_option VALUES (?,?,?,NULL)", (item_id, oi, code))
                for oj, text in enumerate(it.free_text_options, start=len(it.options)):
                    cur.execute("INSERT INTO requirement_option VALUES (?,?,NULL,?)", (item_id, oj, text))

    for e in catalog.exclusions:
        cur.execute("INSERT INTO course_program_exclusion VALUES (?,?,?,?)",
                    (e.course_id, e.program_id, e.max_hours, e.note))
    for k in catalog.core:
        cur.execute("INSERT INTO core_category VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            k.id, year, k.family, k.name, k.courses_required, k.tag, k.departments,
            cnf_to_json(k.course_cnf), k.timing, int(k.placeholder), k.rules_note, k.source, k.needs_review))
    for r in catalog.rules.values():
        cur.execute("INSERT INTO degree_rule VALUES (?,?,?,?)", (r.key, r.value, r.source_key, r.source_text))
    con.commit()
    con.close()
    return path


# --- load ---------------------------------------------------------------------

def load_catalog(path: Path | None = None) -> Catalog:
    """Read a database file written by save_catalog back into a Catalog."""
    path = path or default_db_path()
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run: python scripts/build_db.py")
    con = sqlite3.connect(path)
    cur = con.cursor()
    year = cur.execute("SELECT year FROM bulletin").fetchone()[0]
    prereqs = _load_cnf(cur, "prerequisite")
    coreqs = _load_cnf(cur, "corequisite")
    offerings: dict[str, list[Offering]] = {}
    for cid, term, parity, source in cur.execute("SELECT * FROM offering_pattern"):
        offerings.setdefault(cid, []).append(Offering(term, parity, source))
    exclusive: dict[str, list[str]] = {}
    for a, b in cur.execute("SELECT * FROM exclusive_pair"):
        exclusive.setdefault(a, []).append(b)
        exclusive.setdefault(b, []).append(a)

    courses = {}
    for row in cur.execute("SELECT * FROM course ORDER BY subject, number").fetchall():
        (cid, _, subject, number, title, cmin, cmax, desc, tags, praw, pstatus, craw,
         o_allowed, o_required, standing, notes, source_key) = row
        courses[cid] = Course(
            id=cid, subject=subject, number=number, title=title, credits_min=cmin, credits_max=cmax,
            description=desc, tags=json.loads(tags), prereq=prereqs.get(cid, []), prereq_raw=praw,
            prereq_status=pstatus, coreq=coreqs.get(cid, []), coreq_raw=craw,
            override_allowed=bool(o_allowed), override_required=bool(o_required),
            offerings=offerings.get(cid, []), min_standing=standing,
            exclusive_with=exclusive.get(cid, []), notes=json.loads(notes), source_key=source_key)

    programs = {}
    for pid, _, name, ptype, degree, subject, intro, source_key in cur.execute("SELECT * FROM program").fetchall():
        prog = Program(pid, name, ptype, degree, subject, intro, source_key)
        for gid, label in cur.execute("SELECT id, label FROM requirement_group WHERE program_id=? "
                                      "ORDER BY sort_order", (pid,)).fetchall():
            group = RequirementGroup(label)
            for iid, kind, ilabel, n, hours, isubj, lvl, review in cur.execute(
                    "SELECT id, kind, label, n, hours, subject, min_level, needs_review FROM requirement_item "
                    "WHERE group_id=? ORDER BY sort_order", (gid,)).fetchall():
                item = RequirementItem(kind, ilabel, n=n, hours=hours, subject=isubj, min_level=lvl,
                                       needs_review=review)
                for code, text in cur.execute("SELECT course_id, free_text FROM requirement_option "
                                              "WHERE item_id=? ORDER BY option_no", (iid,)).fetchall():
                    (item.options if code else item.free_text_options).append(code or text)
                group.items.append(item)
            prog.groups.append(group)
        programs[pid] = prog

    exclusions = [ProgramExclusion(*r) for r in cur.execute("SELECT * FROM course_program_exclusion")]
    core = []
    for (kid, _, family, name, count, tag, depts, cnf, timing, placeholder, note, source,
         review) in cur.execute("SELECT * FROM core_category").fetchall():
        core.append(CoreCategory(kid, family, name, count, tag=tag, departments=depts,
                                 course_cnf=cnf_from_json(cnf), timing=timing, placeholder=bool(placeholder),
                                 rules_note=note, source=source, needs_review=review))
    rules = {k: DegreeRule(k, v, sk, st) for k, v, sk, st in cur.execute("SELECT * FROM degree_rule")}
    con.close()
    return Catalog(year, courses, programs, core, rules, exclusions)
