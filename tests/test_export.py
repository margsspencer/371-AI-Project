"""Milestone 8: academic calendar, CSV export, and .ics export."""

import csv
import io
from datetime import date

from icalendar import Calendar

from gradplanner.export import plan_to_csv, plan_to_ics
from gradplanner.parser.calendar import parse_calendar_page, term_events, term_span
from gradplanner.planner.plan import Plan, PlanItem, TermPlan
from gradplanner.planner.terms import Term
from test_planner import catalog

PAGE = """<html><body>
<h2>Fall Semester 2026</h2><table>
<tr><th>Date</th><th>Day</th><th>Event</th></tr>
<tr><td>August 24</td><td>Monday</td><td>Classes begin</td></tr>
<tr><td>September 1</td><td>Tuesday</td><td>Last day to add full-term classes*</td></tr>
<tr><td>September 29</td><td>Tuesday</td><td>Last day to drop a full-term class*</td></tr>
<tr><td>October 27</td><td>Tuesday</td><td>Last day to drop with a grade of "W"</td></tr>
<tr><td>December 7</td><td>Monday</td><td>Classes end</td></tr>
<tr><td>December 9-12</td><td>Wednesday-Saturday</td><td>Fall final exams</td></tr>
<tr><td>December 14-15</td><td>Monday-Tuesday</td><td>Fall final exams</td></tr>
<tr><td>December 16-Jan 10</td><td></td><td>Winter Recess</td></tr>
</table>
<h2>Spring Semester 2027</h2><table>
<tr><td>January 11</td><td>Monday</td><td>Classes begin</td></tr>
<tr><td>April 26</td><td>Wednesday</td><td>Classes end</td></tr>
<tr><td>May 3-5</td><td>Monday-Wednesday</td><td>Spring final exams</td></tr>
<tr><td>May 17</td><td>Monday</td><td>Commencement</td></tr>
<tr><td>Sometime</td><td></td><td>Mystery</td></tr>
</table></body></html>"""


def events():
    return parse_calendar_page(PAGE)


def test_calendar_parsing():
    ev, problems = events()
    assert len(ev) == 12
    recess = next(e for e in ev if e.name == "Winter Recess")
    assert (recess.start, recess.end) == (date(2026, 12, 16), date(2027, 1, 10))   # crosses the new year
    assert next(e for e in ev if e.name.startswith("Last day to add")).name == "Last day to add full-term classes"
    assert any("April 26 2027 is a Monday" in p for p in problems)                  # the Bulletin's typo
    assert any("Sometime" in p for p in problems)
    assert term_span(term_events(ev, "fall", 2026)) == (date(2026, 8, 24), date(2026, 12, 15))


def test_later_years_are_projected():
    ev, _ = events()
    fall = term_events(ev, "fall", 2028)
    assert all(e.estimated for e in fall)
    begin = fall[0].start
    assert begin.weekday() == 0 and abs((begin - date(2028, 8, 24)).days) <= 3        # a Monday near Aug 24
    assert term_events(ev, "summer", 2027) == []


def _plan():
    c = lambda cid, h=3: PlanItem(cid, cid + " title", h, "course", ["TST-BS:0.0"])   # noqa: E731
    return Plan("Test Student (fictional)", "TST-BS", "optimal", [
        TermPlan(Term("fall", 2026), [c("TST 101"), PlanItem("DIV-V", "Division V (choose a course)", 3,
                                                             "core_slot", ["DIV-V"])]),
        TermPlan(Term("summer", 2027), [c("TST 201")]),
        TermPlan(Term("fall", 2027), [PlanItem("ABROAD", "Abroad course: elective", 3, "abroad")], abroad="London"),
        TermPlan(Term("spring", 2028), [c("TST 301", 4)]),
    ], labels={"TST-BS:0.0": "Required: TST 101", "DIV-V": "Division V"})


def test_csv_export():
    rows = list(csv.reader(io.StringIO(plan_to_csv(_plan(), {"TST 301"}))))
    assert rows[0][:5] == ["Semester", "Type", "Course", "Title", "Hours"]
    assert rows[1] == ["Fall 2026", "Course", "TST 101", "TST 101 title", "3", "Required: TST 101", "", ""]
    assert rows[2][1:3] == ["Core (choose a course)", ""] and rows[2][5] == "Division V"
    assert rows[4][1] == "Study abroad" and rows[4][7] == "London"
    assert rows[5][6] == "yes"                                                       # career pick
    assert "Advisory only" in rows[-1][0]


def test_ics_export():
    cat = catalog()
    cat.calendar, _ = events()
    cal = Calendar.from_ical(plan_to_ics(_plan(), cat))
    summaries = [str(e["summary"]) for e in cal.walk("VEVENT")]
    fall = next(e for e in cal.walk("VEVENT") if str(e["summary"]).startswith("Fall 2026:"))
    assert fall.decoded("dtstart") == date(2026, 8, 24) and fall.decoded("dtend") == date(2026, 12, 16)
    assert "TST 101" in str(fall["description"]) and "Advisory only" in str(fall["description"])
    assert "Last day to add classes (Fall 2026)" in summaries
    assert not any("Summer 2027" in s for s in summaries)                           # no published dates
    assert any(s.startswith("Fall 2027 abroad") and "(estimated)" in s for s in summaries)
    assert not any("Last day to add classes (Fall 2027)" in s for s in summaries)   # abroad: no WFU deadlines
    assert any(s.startswith("Commencement (estimated)") for s in summaries)          # last semester is a spring
    assert len(set(str(e["uid"]) for e in cal.walk("VEVENT"))) == len(summaries)    # unique ids
