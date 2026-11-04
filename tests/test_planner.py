"""Planner + independent plan checker on a small made-up catalog (no real student data)."""

import copy

import pytest

from gradplanner.models import Catalog, Course, DegreeRule, Offering
from gradplanner.parser.core import CoreCategory
from gradplanner.parser.prereq import parse_prereq
from gradplanner.parser.programs import Program, RequirementGroup, RequirementItem
from gradplanner.planner import plan_for_student, validate_plan
from gradplanner.planner.requirements import PlannerSettings
from gradplanner.planner.student import CompletedCourse, StudentProfile
from gradplanner.planner.terms import Term, terms_between

FALL_SPRING = [Offering("fall"), Offering("spring")]
SETTINGS = PlannerSettings({"default": 3.0}, {"TST": "Testing", "MTH": "Mathematics"})


def course(cid, prereq=None, credits=3, tags=("D",), offerings=None, coreq=None, **kw):
    subject, number = cid.split()
    return Course(cid, subject, number, cid + " title", credits, credits, "desc", list(tags),
                  parse_prereq(prereq).cnf, prereq, "ok", parse_prereq(coreq).cnf, coreq, False,
                  kw.pop("override_required", False), offerings or FALL_SPRING, **kw)


def catalog():
    courses = [
        course("TST 101"),
        course("TST 201", "TST 101"),
        course("TST 210", "TST 101", coreq="TST 211"),
        course("TST 211", credits=1, tags=()),
        course("TST 301", "TST 201", offerings=[Offering("fall")]),
        course("TST 302", "TST 201", exclusive_with=["TST 303"]),
        course("TST 303", "TST 201", exclusive_with=["TST 302"]),
        course("TST 390", credits=0, tags=(), min_standing="senior"),
        course("TST 395", "TST 201", override_required=True),
        course("MTH 101", tags=("D", "QR")),
    ]
    program = Program("TST-BS", "Testing, B.S.", "major", "BS", "TST", "intro", "fixture", [
        RequirementGroup("Required", [
            RequirementItem("course", "TST 101", ["TST 101"]),
            RequirementItem("course", "TST 201", ["TST 201"]),
            RequirementItem("course", "TST 210", ["TST 210"]),
            RequirementItem("course", "TST 301", ["TST 301"]),
            RequirementItem("course", "TST 390", ["TST 390"]),
            RequirementItem("hours", "3 h of TST 300+", hours=3, subject="TST", min_level=300),
        ]),
        RequirementGroup("Co-Requirements", [RequirementItem("course", "MTH 101", ["MTH 101"])]),
    ])
    core = [
        CoreCategory("BASIC-FYS", "basic", "First-year seminar", 1, timing="year1"),
        CoreCategory("DIV-V", "divisional", "V. Math and Natural Sciences", 2, tag="D",
                     departments="Testing; Mathematics", placeholder=True),
        CoreCategory("QR", "divisional-wide", "Quantitative Reasoning", 1, tag="QR", placeholder=True),
    ]
    rules = {k: DegreeRule(k, v, "fixture", "") for k, v in {
        "total_hours": 36, "max_hours_per_semester": 12, "normal_hours_per_semester": 9,
        "standing_senior_hours": 15, "min_hours_for_core_course": 3}.items()}
    return Catalog("test", {c.id: c for c in courses}, {"TST-BS": program}, core, rules, [])


def student(**kw):
    base = dict(name="Test Student (fictional)", program="TST-BS", entry_term=Term("fall", 2026),
                next_term=Term("fall", 2026), graduation_term=Term("spring", 2028), max_hours_per_term=12,
                language_courses_needed=0)
    base.update(kw)
    return StudentProfile(**base)


def solve(st=None, cat=None):
    cat = cat or catalog()
    st = st or student()
    return cat, st, plan_for_student(cat, st, SETTINGS, time_limit=10, workers=4)


@pytest.fixture(scope="module")
def solved():
    return solve()


# ---------------------------------------------------------------- terms

def test_term_order_and_summer():
    assert Term.parse("Fall 2026").next() == Term("spring", 2027)
    assert Term("spring", 2027).next(include_summer=True) == Term("summer", 2027)
    assert [str(t) for t in terms_between(Term("fall", 2026), Term("fall", 2027))] == \
        ["Fall 2026", "Spring 2027", "Fall 2027"]
    assert Term("spring", 2027).academic_year == 2026


# ---------------------------------------------------------------- valid plan

def test_plan_is_valid(solved):
    cat, st, plan = solved
    assert plan.ok, plan.explanations
    assert validate_plan(plan, cat, st, SETTINGS) == []


def test_prerequisites_come_first(solved):
    _, _, plan = solved
    assert plan.term_of("TST 101") < plan.term_of("TST 201") < plan.term_of("TST 301")


def test_corequisite_same_term_or_earlier(solved):
    _, _, plan = solved
    assert plan.term_of("TST 211") <= plan.term_of("TST 210")


def test_fall_only_course_in_fall(solved):
    _, _, plan = solved
    assert plan.term_of("TST 301").season == "fall"


def test_load_limit(solved):
    _, _, plan = solved
    assert all(tp.hours <= 12 for tp in plan.terms)


def test_senior_standing(solved):
    _, _, plan = solved
    term = plan.term_of("TST 390")
    before = sum(tp.hours for tp in plan.terms if tp.term < term)
    assert before >= 15


def test_listed_courses_do_not_fill_elective_hours(solved):
    # TST 301 is required on its own, so the 300+ elective needs a different course.
    _, _, plan = solved
    assert plan.term_of("TST 302") or plan.term_of("TST 303")


def test_credit_exclusion_pair():
    _, _, plan = solve(student(interests=["TST 302", "TST 303"]))
    assert not (plan.term_of("TST 302") and plan.term_of("TST 303"))


def test_permission_course_never_planned():
    _, _, plan = solve(student(interests=["TST 395"]))
    assert plan.term_of("TST 395") is None
    assert any("TST 395" in n for n in plan.notes)


def test_first_year_seminar_in_first_year(solved):
    _, st, plan = solved
    for tp in plan.terms:
        if any(i.id == "BASIC-FYS" for i in tp.items):
            assert st.year_index(tp.term) == 0


def test_division_needs_two_departments(solved):
    _, _, plan = solved
    div = [i.id for tp in plan.terms for i in tp.items if "DIV-V" in i.counts_toward and i.kind == "course"]
    assert len({c.split()[0] for c in div}) == len(div)


def test_ap_credit_does_not_count_for_division():
    _, _, plan = solve(student(completed=[CompletedCourse("MTH 101", "ap")]))
    assert plan.ok
    assert "DIV-V" not in plan.completed_counts["MTH 101"]
    assert "QR" in plan.completed_counts["MTH 101"] or any(
        "QR" in i.counts_toward for tp in plan.terms for i in tp.items)


# ---------------------------------------------------------------- no valid plan

def test_chain_too_long_is_explained():
    _, _, plan = solve(student(graduation_term=Term("spring", 2027)))
    assert plan.status == "infeasible"
    assert any("TST 101 -> TST 201 -> TST 301" in e for e in plan.explanations)


def test_not_enough_hours_is_explained():
    _, _, plan = solve(student(max_hours_per_term=6))
    assert plan.status == "infeasible"
    assert any("maximum load" in e for e in plan.explanations)


def test_solver_explains_offering_conflict():
    # Spring, Fall, Spring: the chain fits in 3 terms, but TST 301 (fall only) would land in spring.
    _, _, plan = solve(student(entry_term=Term("spring", 2027), next_term=Term("spring", 2027),
                               graduation_term=Term("spring", 2028)))
    assert plan.status == "infeasible"
    text = plan.explanations[0]
    assert "prerequisites" in text and "major/minor" in text
    assert "maximum hours" not in text and "120-hour" not in text     # only the groups that conflict


# ---------------------------------------------------------------- checker catches bad plans

def _move(plan, cid, to_index):
    bad = copy.deepcopy(plan)
    item = next(i for tp in bad.terms for i in tp.items if i.id == cid)
    for tp in bad.terms:
        tp.items = [i for i in tp.items if i is not item]
    bad.terms[to_index].items.append(item)
    return bad


def test_checker_catches_prerequisite_order(solved):
    cat, st, plan = solved
    problems = validate_plan(_move(plan, "TST 301", 0), cat, st, SETTINGS)
    assert any("prerequisite not met" in p for p in problems)


def test_checker_catches_offering_and_missing_course(solved):
    cat, st, plan = solved
    spring = next(i for i, tp in enumerate(plan.terms) if tp.term.season == "spring" and i > 1)
    problems = validate_plan(_move(plan, "TST 301", spring), cat, st, SETTINGS)
    assert any("not offered" in p for p in problems)

    bad = copy.deepcopy(plan)
    for tp in bad.terms:
        tp.items = [i for i in tp.items if i.id != "MTH 101"]
    assert any("Not met: Co-Requirements" in p for p in validate_plan(bad, cat, st, SETTINGS))


def test_checker_catches_overload_and_duplicates(solved):
    cat, st, plan = solved
    bad = copy.deepcopy(plan)
    extra = next(i for tp in bad.terms for i in tp.items if i.id == "TST 101")
    bad.terms[-1].items += [copy.deepcopy(extra) for _ in range(4)]
    problems = validate_plan(bad, cat, st, SETTINGS)
    assert any("over the limit" in p for p in problems)
    assert any("also planned" in p for p in problems)


# ---------------------------------------------------------------- real data (skipped if the DB isn't built)

def test_real_bs_plan_for_sample_student():
    from gradplanner.db.store import default_db_path, load_catalog
    from gradplanner.planner import load_profile
    from gradplanner import config

    if not default_db_path().exists():
        pytest.skip("run scripts/build_db.py first")
    cat = load_catalog()
    st = load_profile(config.PROJECT_ROOT / "samples" / "sam_first_year_bs.yaml")
    plan = plan_for_student(cat, st, time_limit=10)
    assert plan.ok, plan.explanations
    assert len(plan.terms) == 8
    assert validate_plan(plan, cat, st) == []
