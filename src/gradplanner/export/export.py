"""Write a plan as CSV (one row per course) or as an iCalendar file.

The calendar has, for each planned semester:
  - one all-day event spanning the semester (first day of classes to the last final
    exam) whose description lists the planned courses and hours
  - all-day reminders for the add, drop, and withdraw ("W") deadlines and finals
  - Commencement after the last spring semester
Dates come from the Bulletin's 2026-27 calendar; later years are projected and
every projected event says "(estimated)". Summer terms have no published dates and
are skipped (the CSV still lists them).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from icalendar import Calendar, Event

from gradplanner.models import Catalog
from gradplanner.parser.calendar import find, term_events, term_span
from gradplanner.planner.plan import Plan

ADVISORY = "Advisory only: confirm with your academic advisor and the current Bulletin."
KIND_LABEL = {"course": "Course", "core_slot": "Core (choose a course)", "elective_slot": "Free elective",
              "abroad": "Study abroad"}
REMINDERS = [(("last day to add",), "Last day to add classes"),
             (("last day to drop a full",), "Last day to drop a class"),
             (("drop with a grade",), "Last day to withdraw (grade of W)")]


def plan_to_csv(plan: Plan, recommended: set[str] = frozenset()) -> str:
    """One row per planned item, in semester order."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Semester", "Type", "Course", "Title", "Hours", "Counts toward", "Career pick", "Study abroad"])
    for tp in plan.terms:
        for item in tp.items:
            w.writerow([str(tp.term), KIND_LABEL.get(item.kind, item.kind),
                        item.id if item.kind == "course" else "", item.title, f"{item.hours:g}",
                        "; ".join(plan.labels.get(k, k) for k in item.counts_toward),
                        "yes" if item.id in recommended else "", tp.abroad])
    w.writerow([])
    w.writerow([f"{plan.student_name}: {plan.program_id}. {ADVISORY}"])
    return out.getvalue()


def _uid(*parts) -> str:
    return "-".join(str(p).replace(" ", "_") for p in parts) + "@gradplanner.local"


def _all_day(cal: Calendar, uid: str, summary: str, start, end=None, description: str = "") -> None:
    ev = Event()
    ev.add("uid", uid)
    ev.add("dtstamp", datetime.now(timezone.utc))
    ev.add("summary", summary)
    ev.add("dtstart", start)
    ev.add("dtend", (end or start) + timedelta(days=1))          # all-day: end is exclusive
    ev.add("transp", "TRANSPARENT")                             # don't show as busy
    if description:
        ev.add("description", description)
    cal.add_component(ev)


def plan_to_ics(plan: Plan, catalog: Catalog) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//WFU Graduation Calendar Planner (class project)//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", f"Graduation plan: {plan.student_name}")
    last_spring = None
    for tp in plan.terms:
        events = term_events(catalog.calendar, tp.term.season, tp.term.year)
        span = term_span(events)
        if span is None:
            continue                                            # summer: no published dates
        est = " (estimated)" if events[0].estimated else ""
        lines = [f"{i.id + ' ' if i.kind == 'course' else ''}{i.title} ({i.hours:g} h)" for i in tp.items]
        where = f"Study abroad: {tp.abroad}\n" if tp.abroad else ""
        desc = (f"{where}{tp.hours:g} hours planned:\n" + "\n".join(lines)
                + (f"\n\nDates are projected from the 2026-27 Bulletin calendar." if est else "")
                + f"\n\n{ADVISORY}")
        _all_day(cal, _uid("term", tp.term), f"{tp.term}{' abroad' if tp.abroad else ''}: "
                 f"{len([i for i in tp.items if i.kind == 'course'])} courses, {tp.hours:g} h{est}",
                 span[0], span[1], desc)
        if not tp.abroad:                                       # Wake Forest deadlines don't apply abroad
            for words, label in REMINDERS:
                e = find(events, *words)
                if e:
                    _all_day(cal, _uid(label, tp.term), f"{label} ({tp.term}){est}", e.start,
                             description=ADVISORY)
            exams = [e for e in events if "final exam" in e.name.lower()]
            if exams:
                _all_day(cal, _uid("finals", tp.term), f"Final exams ({tp.term}){est}",
                         min(e.start for e in exams), max(e.end for e in exams))
        if tp.term.season == "spring":
            last_spring = (tp, events, est)
    if last_spring and plan.terms[-1].term == last_spring[0].term:
        tp, events, est = last_spring
        e = find(events, "commencement")
        if e:
            _all_day(cal, _uid("commencement", tp.term), f"Commencement{est} 🎓", e.start,
                     description="Your planned graduation. " + ADVISORY)
    return cal.to_ical()
