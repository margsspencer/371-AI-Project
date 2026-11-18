"""AP exams -> credit (Wake Forest AP policy 2025-26)."""

import pytest

from gradplanner.planner import load_profile, plan_for_student, validate_plan
from gradplanner.planner.ap import ap_awards, ap_divisional_subjects, apply_ap, exam_names
from gradplanner.planner.requirements import core_needs, course_counts_for_core
from gradplanner.planner.student import ApExam, CompletedCourse, save_profile
from gradplanner.ui.helpers import build_profile
from test_planner import SETTINGS, catalog, student
from test_ui import FORM


def awards(*exams):
    return ap_awards([ApExam(e, s) for e, s in exams])


def test_scores_bands_and_combined_economics():
    got, notes = awards(("Calculus BC", 4), ("Biology", 3))
    assert got[0].courses == [("MTH 112", 4.0), ("MTH 111", 0.0)]
    assert any("Biology (3): no Wake Forest credit" in n for n in notes)
    got, _ = awards(("Economics: Microeconomics", 4), ("Economics: Macroeconomics", 4))
    assert [a.courses for a in got] == [[("ECN 150", 3.0)]]
    got, notes = awards(("Economics: Microeconomics", 4), ("Economics: Macroeconomics", 3))
    assert got == [] and any("combined score 7" in n for n in notes)
    got, _ = awards(("Calculus BC", 3), ("Calculus BC", 5))          # the higher score counts
    assert got[0].score == 5


def test_credit_hours_language_and_old_course_numbers():
    known = {"MTH 111", "MTH 112", "CHI 153", "CHI 201"}
    st = apply_ap(student(ap_exams=[ApExam("Calculus BC", 5), ApExam("Chinese Language and Culture", 4),
                                    ApExam("Spanish Language and Culture", 4)]), known)
    by_id = {c.course: c for c in st.completed}
    assert by_id["MTH 111"].hours == 0 and by_id["MTH 112"].hours == 4 and by_id["MTH 111"].source == "ap"
    assert (st.language, st.language_placement) == ("CHI", "CHI 201")       # first language exam wins
    assert [o.name for o in st.outside_courses] == ["AP Spanish Language and Culture (4): SPA 154"]
    again = apply_ap(st, known)                                               # idempotent
    assert len(again.completed) == 3 and len(again.outside_courses) == 1


def test_ap_never_lowers_a_placement():
    st = student(language="CHI", language_placement="CHI 201", ap_exams=[ApExam("Chinese Language and Culture", 3)])
    assert apply_ap(st, {"CHI 102"}).language_placement == "CHI 201"


def test_divisional_after_two_ap_courses_in_a_department():
    cat = catalog()
    st = student(completed=[CompletedCourse("TST 101", "ap"), CompletedCourse("TST 210", "ap")])
    subjects = ap_divisional_subjects(st)
    assert subjects == {"TST"}
    div = next(n for n in core_needs(cat, st, SETTINGS) if n.id == "DIV-V")
    plain = cat.courses["TST 302"]
    plain.tags = []                                                   # a 300-level course without (D)
    assert course_counts_for_core(plain, div, cat, SETTINGS, "wfu", subjects)
    assert not course_counts_for_core(plain, div, cat, SETTINGS, "wfu", set())
    assert not course_counts_for_core(cat.courses["TST 395"], div, cat, SETTINGS, "wfu", subjects) \
        or "D" in cat.courses["TST 395"].tags                         # 395 is excluded by the policy
    assert not course_counts_for_core(cat.courses["TST 101"], div, cat, SETTINGS, "ap", subjects)  # AP itself


def test_form_and_save_round_trip(tmp_path):
    st = build_profile(**{**FORM, "ap_exams": [{"exam": "Computer Science A", "score": 5},
                                               {"exam": None, "score": None}]})
    assert [a.exam for a in st.ap_exams] == ["Computer Science A"]
    assert any(c.course == "CSC 111" and c.from_exam for c in st.completed)
    path = tmp_path / "p.yaml"
    save_profile(st, path)
    back = load_profile(path)
    assert back.ap_exams == st.ap_exams
    assert sum(1 for c in back.completed if c.course == "CSC 111") == 1     # not saved twice


def test_exam_names_cover_the_policy():
    names = exam_names()
    assert "Calculus BC" in names and "Spanish Literature and Culture" in names and len(names) == 34


# ---------------------------------------------------------------- real data

@pytest.fixture(scope="module")
def real():
    from gradplanner.db.store import default_db_path, load_catalog
    if not default_db_path().exists():
        pytest.skip("run scripts/build_db.py first")
    return load_catalog()


def test_real_policy_courses_exist(real):
    from gradplanner.planner.ap import ap_table
    missing = {c for bands in ap_table()["exams"].values() for b in bands for c, _ in b.get("credit") or []
               if c not in real.courses}
    assert missing == {"SPA 154", "SPA 212"}                         # old numbers: outside credit instead


def test_real_plan_with_ap_credit(real):
    st = load_profile("samples/sam_first_year_bs.yaml")
    st.ap_exams = [ApExam("Calculus BC", 5), ApExam("Computer Science A", 4)]
    apply_ap(st)
    plan = plan_for_student(real, st)
    assert plan.ok and validate_plan(plan, real, st) == []
    planned = {c for tp in plan.terms for c in tp.course_ids}
    assert not planned & {"CSC 111", "MTH 111", "MTH 112"}
    assert st.course_hours(real)["MTH 111"] == 0
