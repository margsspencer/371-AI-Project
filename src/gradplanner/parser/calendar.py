"""Academic calendar (/undergraduate/calendar/): dates for each semester.

The Bulletin page has one table per semester ("Fall Semester 2026", "Spring Semester
2027") with rows like "August 24 | Monday | Classes begin" or "December 9-12 |
Wednesday-Saturday | Fall final exams". We keep every row as an event with a start
and end date. Later years aren't published, so `term_events` projects them from
the 2026-27 dates (same weekday, about a year later) and marks them estimated.
Summer sessions have no table on this page, so summer terms get no dates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from bs4 import BeautifulSoup

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9

TERM_RE = re.compile(r"\b(Fall|Spring|Summer)\b.*?\b(20\d\d)\b", re.I)
DATE_RE = re.compile(r"^\s*([A-Za-z]+)\.?\s+(\d{1,2})(?:\s*[-–]\s*(?:([A-Za-z]+)\.?\s+)?(\d{1,2}))?\s*$")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass
class CalendarEvent:
    season: str          # fall | spring
    year: int            # calendar year of the term (Spring 2027 -> 2027)
    start: date
    end: date
    name: str            # "Classes begin", "Fall final exams", ...
    estimated: bool = False


def _year_for(season: str, term_year: int, month: int) -> int:
    """A fall table's January dates (winter recess) fall in the next year."""
    return term_year + 1 if season == "fall" and month < 7 else term_year


def parse_calendar_page(html: str) -> tuple[list[CalendarEvent], list[str]]:
    """Events from the calendar page, and problems (rows not understood, weekday mismatches)."""
    soup = BeautifulSoup(html, "html.parser")
    events, problems = [], []
    for table in soup.find_all("table"):
        heading = table.find_previous(["h2", "h3", "h4"])
        m = TERM_RE.search(heading.get_text(" ", strip=True) if heading else "")
        if not m or m.group(1).lower() == "summer":
            continue
        season, term_year = m.group(1).lower(), int(m.group(2))
        for tr in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 3 or not cells[0]:
                continue
            when, day, name = cells[0], cells[1], cells[2].rstrip("*").strip()
            d = DATE_RE.match(when)
            if not d or d.group(1).lower() not in MONTHS:
                problems.append(f"calendar: date not understood: {when!r} ({name})")
                continue
            m1 = MONTHS[d.group(1).lower()]
            start = date(_year_for(season, term_year, m1), m1, int(d.group(2)))
            if d.group(4):
                m2 = MONTHS.get((d.group(3) or d.group(1)).lower(), m1)
                end = date(_year_for(season, term_year, m2), m2, int(d.group(4)))
            else:
                end = start
            first_day = day.split("-")[0].strip().lower()
            if first_day in WEEKDAYS and WEEKDAYS.index(first_day) != start.weekday():
                problems.append(f"calendar: {when} {term_year} is a {WEEKDAYS[start.weekday()].title()}, "
                                f"but the Bulletin says {day} ({name}); using the date")
            events.append(CalendarEvent(season, term_year, start, end, name))
    return events, problems


# ---------------------------------------------------------------- lookups

def _shift(d: date, years: int) -> date:
    """About `years` years later, on the same weekday (52-week steps, kept within a few days)."""
    if years == 0:
        return d
    shifted = d + timedelta(weeks=52 * years)
    target = date(d.year + years, d.month, min(d.day, 28))
    while (shifted - target).days < -3:
        shifted += timedelta(weeks=1)
    while (shifted - target).days > 3:
        shifted -= timedelta(weeks=1)
    return shifted


def term_events(events: list[CalendarEvent], season: str, year: int) -> list[CalendarEvent]:
    """The events of one semester: from the Bulletin if published, otherwise projected."""
    exact = [e for e in events if e.season == season and e.year == year]
    if exact:
        return exact
    base = [e for e in events if e.season == season]
    if not base:
        return []
    k = year - base[0].year
    return [CalendarEvent(season, year, _shift(e.start, k), _shift(e.end, k), e.name, True) for e in base]


def find(events: list[CalendarEvent], *words: str) -> CalendarEvent | None:
    """The first event whose name contains all `words` (case-insensitive)."""
    for e in events:
        if all(w.lower() in e.name.lower() for w in words):
            return e
    return None


def term_span(events: list[CalendarEvent]) -> tuple[date, date] | None:
    """(first day of classes, last day of final exams) for one semester's events."""
    begin = find(events, "classes begin")
    if begin is None:
        return None
    exams = [e.end for e in events if "final exam" in e.name.lower()]
    classes_end = find(events, "classes end")
    end = max(exams) if exams else (classes_end.end if classes_end else begin.end)
    return begin.start, end
