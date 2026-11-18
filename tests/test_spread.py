"""A department's courses are spaced out: at most two per semester when there is room."""

from collections import Counter

import pytest

from gradplanner.parser.programs import RequirementGroup, RequirementItem
from gradplanner.planner import plan_for_student, validate_plan
from gradplanner.planner.model import _Builder
from gradplanner.planner.student import load_profile
from gradplanner.planner.terms import Term
from test_planner import SETTINGS, catalog, course, student

BEM = ["BEM 201", "BEM 211", "BEM 221", "BEM 241", "BEM 251", "BEM 261"]


def bem_plan():
    cat = catalog()
    for cid in BEM:
        cat.courses[cid] = course(cid, tags=())
    cat.programs["TST-BS"].groups.append(
        RequirementGroup("Business core", [RequirementItem("course", c, [c]) for c in BEM]))
    st = student(graduation_term=Term("spring", 2029))
    return cat, st, plan_for_student(cat, st, SETTINGS, time_limit=10, workers=4)


def per_term(plan):
    return [Counter(c.split()[0] for c in tp.course_ids) for tp in plan.terms]


def test_same_department_courses_are_spread_out():
    cat, st, plan = bem_plan()
    assert plan.ok and validate_plan(plan, cat, st, SETTINGS) == []
    assert max(n["BEM"] for n in per_term(plan)) <= _Builder.SAME_SUBJECT_PER_TERM


def test_it_is_a_preference_not_a_rule():
    """Six BEM courses and nothing else in two semesters: three each, and the plan still works."""
    cat = catalog()
    for cid in BEM:
        cat.courses[cid] = course(cid, tags=())
    cat.programs["TST-BS"].groups = [RequirementGroup("Business core", [RequirementItem("course", c, [c]) for c in BEM])]
    cat.core = []
    cat.rules["total_hours"].value = 18
    st = student(graduation_term=Term("spring", 2027))
    plan = plan_for_student(cat, st, SETTINGS, time_limit=10, workers=4)
    assert plan.ok and validate_plan(plan, cat, st, SETTINGS) == []
    assert [n["BEM"] for n in per_term(plan)] == [3, 3]


# ---------------------------------------------------------------- real data

@pytest.fixture(scope="module")
def real():
    from gradplanner.db.store import default_db_path, load_catalog
    if not default_db_path().exists():
        pytest.skip("run scripts/build_db.py first")
    return load_catalog()


def test_real_finance_spreads_bem(real):
    """Riley's plan used to put three BEM courses in each of three semesters."""
    st = load_profile("samples/riley_first_year_finance.yaml")
    plan = plan_for_student(real, st)
    assert plan.ok and validate_plan(plan, real, st) == []
    assert max(n["BEM"] for n in per_term(plan)) <= 2
