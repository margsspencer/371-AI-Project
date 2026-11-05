"""Study abroad semesters: abroad-course slots, credit limits, and the independent checks."""

import pytest

from gradplanner.audit import run_audit
from gradplanner.planner import plan_for_student, validate_plan
from gradplanner.planner.plan import PlanItem
from gradplanner.planner.student import AbroadTerm, StudentProfile, profile_from_dict
from gradplanner.planner.terms import Term

LONDON = Term("spring", 2029)


@pytest.fixture(scope="module")
def cat():
    from gradplanner.db.store import default_db_path, load_catalog
    if not default_db_path().exists():
        pytest.skip("run scripts/build_db.py first")
    c = load_catalog()
    if "FIN-BS" not in c.programs:
        pytest.skip("run scripts/scrape.py --all and scripts/build_db.py for all departments")
    return c


def student(program="CSC-BS", *abroad):
    return StudentProfile(name="Abroad Test (fictional)", program=program, entry_term=Term("fall", 2026),
                          next_term=Term("fall", 2026), graduation_term=Term("spring", 2030),
                          language_courses_needed=1, abroad=list(abroad))


def abroad_term(plan, term):
    return next(tp for tp in plan.terms if tp.term == term)


def test_profile_reads_abroad():
    p = profile_from_dict({"name": "X (fictional)", "program": "CSC-BS", "entry_term": "Fall 2026",
                           "next_term": "Fall 2026", "graduation_term": "Spring 2030",
                           "abroad": [{"term": "Spring 2029", "program": "affiliate", "location": "Madrid"}]})
    assert p.abroad == [AbroadTerm(LONDON, "affiliate", "Madrid")]
    with pytest.raises(ValueError):
        AbroadTerm(LONDON, "exchange")


def test_wfu_semester_abroad(cat):
    st = student("CSC-BS", AbroadTerm(LONDON, "wfu", "London"))
    plan = plan_for_student(cat, st)
    assert plan.ok and validate_plan(plan, cat, st) == []
    tp = abroad_term(plan, LONDON)
    assert tp.abroad == "London" and tp.hours >= 12
    assert {i.kind for i in tp.items} == {"abroad"}                     # no Wake Forest catalog courses
    assert any(k.startswith("DIV-") for i in tp.items for k in i.counts_toward)   # divisionals abroad
    audit = run_audit(cat, st, plan)
    assert audit.count("remaining") == 0
    assert any(e.term == str(LONDON) for line in audit.lines for e in line.used)


def test_business_major_abroad_limit(cat):
    st = student("FIN-BS", AbroadTerm(Term("fall", 2028), "affiliate"))
    plan = plan_for_student(cat, st)
    assert plan.ok and validate_plan(plan, cat, st) == []
    toward_major = sum(i.hours for tp in plan.terms for i in tp.items
                       if i.kind == "abroad" and any(k.startswith("FIN-BS:") for k in i.counts_toward))
    assert toward_major <= 6
    tp = abroad_term(plan, Term("fall", 2028))
    assert not any(k.startswith("21CS") for i in tp.items for k in i.counts_toward)   # transfer credit


def test_affiliate_senior_year_refused(cat):
    plan = plan_for_student(cat, student("CSC-BS", AbroadTerm(Term("fall", 2029), "affiliate")))
    assert not plan.ok and "senior year" in plan.explanations[0]
    plan = plan_for_student(cat, student("CSC-BS", AbroadTerm(Term("fall", 2031), "wfu")))
    assert not plan.ok and "outside the semesters" in plan.explanations[0]


def test_checker_catches_abroad_mistakes(cat):
    st = student("FIN-BS", AbroadTerm(Term("fall", 2028), "affiliate"))
    plan = plan_for_student(cat, st)
    tp = abroad_term(plan, Term("fall", 2028))
    tp.items.append(PlanItem("ABROAD", "Abroad course", 3, "abroad", ["21CS-EI"]))
    tp.items.append(PlanItem("ABROAD", "Abroad course", 3, "abroad", ["FIN-BS:1.0"] * 1))
    tp.items.append(PlanItem("ABROAD", "Abroad course", 3, "abroad", ["FIN-BS:1.0"]))
    home = plan.terms[0]
    home.items.append(PlanItem("ABROAD", "Abroad course", 3, "abroad"))
    problems = " | ".join(validate_plan(plan, cat, st))
    assert "can't count toward 21CS-EI" in problems
    assert "abroad courses in a semester on campus" in problems
    assert "the limit is 6" in problems
