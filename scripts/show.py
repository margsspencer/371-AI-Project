"""Look things up in the built database (handy for checking against the Bulletin).

Usage:
    python scripts/show.py "CSC 374"     # one course: credits, prerequisite logic, offerings
    python scripts/show.py CSC-BS        # one program's requirement groups
    python scripts/show.py core          # core categories and degree rules
"""

import sys

from gradplanner.db.store import load_catalog
from gradplanner.parser.prereq import cnf_to_text


def show_course(catalog, cid: str) -> None:
    c = catalog.courses[cid]
    print(f"{c.id}: {c.title} ({c.credits_min:g}" + (f"-{c.credits_max:g}" if c.credits_max != c.credits_min else "")
          + " h)  tags=" + ",".join(c.tags))
    print(f"  Bulletin text : {c.prereq_raw!r}")
    print(f"  Prerequisite  : {cnf_to_text(c.prereq) or '(none)'}   [{c.prereq_status}]")
    if c.coreq:
        print(f"  Corequisite   : {cnf_to_text(c.coreq)}")
    print("  Offered       : " + ", ".join(f"{o.term} ({o.year_parity}, {o.source})" for o in c.offerings))
    if c.min_standing:
        print(f"  Standing      : {c.min_standing} year")
    if c.override_required:
        print("  Needs department permission")
    for n in c.notes:
        print(f"  note: {n}")


def show_program(catalog, pid: str) -> None:
    p = catalog.programs[pid]
    print(f"{p.name} ({p.id})\n  {p.intro}")
    for g in p.groups:
        print(f"  [{g.label}]")
        for it in g.items:
            extra = ""
            if it.kind == "choose":
                extra = f" choose {it.n} of {it.options + it.free_text_options}"
            elif it.kind == "hours":
                extra = f" {it.hours:g} h of {it.subject} {it.min_level}+"
            print(f"    - {it.label}{extra}" + ("   (NEEDS HUMAN REVIEW)" if it.needs_review else ""))
    ex = catalog.excluded_from(pid)
    if ex:
        print("  Doesn't count: " + ", ".join(f"{c}" + (f" (max {h:g} h)" if h else "") for c, h in ex.items()))


def show_core(catalog) -> None:
    for k in catalog.core:
        what = cnf_to_text(k.course_cnf) or (f"{k.courses_required} course(s) tagged {k.tag}" if k.tag else "slot")
        print(f"  {k.id:10s} {k.name[:45]:45s} {what}" + (f"  [{k.timing}]" if k.timing else "")
              + ("" if k.source == "bulletin" else "  (team decision)"))
    for r in catalog.rules.values():
        print(f"  rule {r.key} = {r.value:g}")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arg = " ".join(sys.argv[1:])
    catalog = load_catalog()
    if arg in catalog.courses:
        show_course(catalog, arg)
    elif arg in catalog.programs:
        show_program(catalog, arg)
    elif arg.lower() == "core":
        show_core(catalog)
    else:
        raise SystemExit(f"'{arg}' is not a course, program ({', '.join(catalog.programs)}), or 'core'")


if __name__ == "__main__":
    main()
