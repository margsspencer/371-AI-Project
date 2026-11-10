"""Overloads: more than the normal hour limit in a few semesters, only when needed."""

import dataclasses

from gradplanner.planner import plan_for_student, validate_plan
from gradplanner.planner.student import AbroadTerm, CompletedCourse, OutsideCourse, profile_from_dict
from gradplanner.planner.terms import Term
from test_planner import SETTINGS, catalog, student

LIMIT = 12          # the synthetic catalog's maximum hours per semester


def tight(**kw):
    """9 hours earned, 36 needed, two semesters left: 27 hours won't fit in 2 x 12."""
    return student(completed=[CompletedCourse("TST 101"), CompletedCourse("TST 201")],
                   outside_courses=[OutsideCourse("Transfer elective", 3)],
                   graduation_term=Term("spring", 2027), **kw)


def solve(st):
    cat = catalog()
    return cat, plan_for_student(cat, st, SETTINGS, time_limit=10, workers=4)


def test_without_overloads_there_is_no_plan():
    cat, plan = solve(tight())
    assert not plan.ok and "allow overloads" in plan.explanations[0]


def test_one_overload_when_needed():
    # 28 hours are needed (3-hour electives): 12 + 16 works with a single overload.
    st = tight(overload_hours=16, overload_terms=2)
    cat, plan = solve(st)
    assert plan.ok and validate_plan(plan, cat, st, SETTINGS) == []
    over = [tp for tp in plan.terms if tp.hours > LIMIT]
    assert len(over) == 1 and over[0].hours == 16                    # one overload, only as big as needed
    assert any("overload" in n and "permission" in n for n in plan.notes)


def test_overload_cap_can_force_two():
    st = tight(overload_hours=15, overload_terms=2)                  # 15 + 13: both semesters over 12
    cat, plan = solve(st)
    assert plan.ok and sum(tp.hours > LIMIT for tp in plan.terms) == 2
    _, plan = solve(tight(overload_hours=15, overload_terms=1))
    assert not plan.ok


def test_no_overload_when_not_needed():
    st = student(overload_hours=15, graduation_term=Term("spring", 2029))
    cat, plan = solve(st)
    assert plan.ok and max(tp.hours for tp in plan.terms) <= LIMIT
    assert not any("overload" in n for n in plan.notes)


def test_checker_enforces_overload_limits():
    st = tight(overload_hours=15, overload_terms=2)
    cat, plan = solve(st)
    assert validate_plan(plan, cat, dataclasses.replace(st, overload_terms=0), SETTINGS)
    assert any("over the limit" in p for p in validate_plan(plan, cat, dataclasses.replace(st, overload_hours=0),
                                                             SETTINGS))


def test_no_overloads_in_summer_or_abroad():
    st = tight(overload_hours=15, abroad=[AbroadTerm(Term("fall", 2026))])
    assert not st.can_overload(Term("fall", 2026)) and st.can_overload(Term("spring", 2027))
    assert not st.can_overload(Term("summer", 2027))


def test_profile_fields():
    d = {"name": "X (fictional)", "program": "CSC-BS", "entry_term": "Fall 2026", "next_term": "Fall 2026",
         "graduation_term": "Spring 2030", "overload_hours": 19, "overload_terms": 1}
    p = profile_from_dict(d)
    assert (p.overload_hours, p.overload_terms) == (19, 1)
    assert profile_from_dict({**d, "overload_hours": None}).overload_hours == 0
