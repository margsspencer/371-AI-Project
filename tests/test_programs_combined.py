"""Several programs at once (double major, major + minor) and the School of Business timing rule."""

import pytest

from gradplanner.planner import load_profile, plan_for_student, validate_plan
from gradplanner.planner.requirements import load_settings, program_problems
from gradplanner.planner.student import StudentProfile, profile_from_dict
from gradplanner.planner.terms import Term

BUSINESS = {"ACC", "BEM", "BUS", "FIN"}


@pytest.fixture(scope="module")
def cat():
    from gradplanner.db.store import default_db_path, load_catalog
    if not default_db_path().exists():
        pytest.skip("run scripts/build_db.py first")
    c = load_catalog()
    if "FIN-BS" not in c.programs:
        pytest.skip("run scripts/scrape.py --all and scripts/build_db.py for all departments")
    return c


def first_year(*programs, **kw):
    return StudentProfile(name="Test (fictional)", program=programs[0], other_programs=list(programs[1:]),
                          entry_term=Term("fall", 2026), next_term=Term("fall", 2026),
                          graduation_term=Term("spring", 2030), **kw)


def test_profile_majors_and_minors():
    d = {"name": "X (fictional)", "majors": ["CSC-BS", "MTH-BA"], "minors": ["STA-MINOR"],
         "entry_term": "Fall 2026", "next_term": "Fall 2026", "graduation_term": "Spring 2030"}
    p = profile_from_dict(d)
    assert p.program == "CSC-BS" and p.programs == ["CSC-BS", "MTH-BA", "STA-MINOR"]
    assert profile_from_dict({**d, "majors": None, "minors": None, "program": "CSC-BS"}).programs == ["CSC-BS"]


def test_business_courses_wait_for_admission(cat):
    st = first_year("FIN-BS")
    plan = plan_for_student(cat, st)
    assert plan.ok and validate_plan(plan, cat, st) == []
    early = [c for tp in plan.terms if tp.term < Term("spring", 2028) for c in tp.course_ids
             if c.split()[0] in BUSINESS]
    assert set(early) <= {"ACC 111", "BEM 201"}, early            # admission prerequisites only
    assert any("sophomore spring" in n for n in plan.notes)


def test_checker_catches_early_business_course(cat):
    st = first_year("FIN-BS")
    plan = plan_for_student(cat, st)
    later = next(tp for tp in plan.terms if "FIN 231" in tp.course_ids)
    item = next(i for i in later.items if i.id == "FIN 231")
    later.items.remove(item)
    plan.terms[0].items.append(item)                              # move it to first-year fall
    assert any("before admission" in p for p in validate_plan(plan, cat, st))


def test_non_business_student_gets_no_business_electives(cat):
    st = first_year("ECN-BA", career="financial-analyst")
    from gradplanner.recommender import recommend_for_student
    recs, relevance = recommend_for_student(cat, st, top_n=10)
    assert not any(r.course_id.split()[0] in BUSINESS - {"ACC"} for r in recs if r.course_id != "BEM 201")
    plan = plan_for_student(cat, st, relevance=relevance)
    assert plan.ok and validate_plan(plan, cat, st) == []


def test_double_major_and_minor(cat):
    st = first_year("CSC-BS", "MTH-BA", "STA-MINOR")
    plan = plan_for_student(cat, st)
    assert plan.ok and validate_plan(plan, cat, st) == []
    for pid in st.programs:                                        # every program's rules are in the plan
        assert any(k.startswith(pid + ":") for k in plan.labels)


def test_forbidden_double_major(cat):
    st = first_year("BIO-BS", "NEU-BS")
    assert program_problems(cat, st, load_settings(cat))
    plan = plan_for_student(cat, st)
    assert not plan.ok and "can't be a double major" in plan.explanations[0]


@pytest.mark.parametrize("path", ["samples/riley_first_year_finance.yaml", "samples/casey_double_major.yaml"])
def test_new_samples_plan(cat, path):
    from gradplanner import config
    st = load_profile(config.PROJECT_ROOT / path)
    plan = plan_for_student(cat, st)
    assert plan.ok and validate_plan(plan, cat, st) == []
