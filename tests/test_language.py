"""Foreign language requirement: a language, a placement level, and the course sequence to a 200-level course."""

import dataclasses

import pytest

from gradplanner.audit.audit import run_audit
from gradplanner.models import CoreCategory
from gradplanner.parser.courses import PREREQ_START_RE, extract_requisite
from gradplanner.planner import plan_for_student, validate_plan
from gradplanner.planner.requirements import LanguageSequence, language_courses, load_settings
from gradplanner.planner.student import CompletedCourse, load_profile
from gradplanner.planner.terms import Term
from gradplanner.ui.helpers import build_profile
from test_planner import SETTINGS, catalog, course, student
from test_ui import FORM

LNG = ["LNG 111", "LNG 112", "LNG 153", "LNG 201"]
SET = dataclasses.replace(SETTINGS, languages={"LNG": LanguageSequence("LNG", "Testish", LNG)})


def lang_catalog():
    cat = catalog()
    for c in [course("LNG 111", tags=()), course("LNG 112", "LNG 111", tags=()),
              course("LNG 153", "LNG 112 or [placement]", tags=()), course("LNG 201", "LNG 153", tags=())]:
        cat.courses[c.id] = c
    cat.core.append(CoreCategory("BASIC-LANG", "basic", "Foreign language (one 200-level course)", 1,
                                 placeholder=True))
    return cat


def lang_student(placement="LNG 112", **kw):
    return student(language="LNG", language_placement=placement, graduation_term=Term("spring", 2029), **kw)


def test_courses_from_placement():
    assert language_courses(lang_student("LNG 112"), SET) == (["LNG 112", "LNG 153", "LNG 201"], {"LNG 111"})
    st = lang_student("LNG 112", completed=[CompletedCourse("LNG 153")])
    assert language_courses(st, SET) == (["LNG 201"], {"LNG 111", "LNG 112", "LNG 153"})
    assert language_courses(lang_student("done"), SET)[0] == []
    assert language_courses(student(language_courses_needed=2), SET) == ([], set())     # no language chosen


def test_sequence_is_planned_in_order_and_placement_counts():
    cat, st = lang_catalog(), lang_student("LNG 112")
    plan = plan_for_student(cat, st, SET, time_limit=10, workers=4)
    assert plan.ok and validate_plan(plan, cat, st, SET) == []
    when = {cid: tp.term for tp in plan.terms for cid in tp.course_ids}
    assert "LNG 111" not in when                                      # placed above it
    assert when["LNG 112"] < when["LNG 153"] < when["LNG 201"]
    assert when["LNG 112"] == Term("fall", 2026)                      # starts right away
    item = next(i for tp in plan.terms for i in tp.items if i.id == "LNG 201")
    assert "BASIC-LANG" in item.counts_toward
    assert not any(i.kind == "core_slot" and i.id == "BASIC-LANG" for tp in plan.terms for i in tp.items)
    line = next(l for l in run_audit(cat, st, plan, SET).lines if l.label.startswith("Foreign language"))
    assert line.status == "planned" and "LNG 112" in line.detail and "LNG 201" in line.detail


def test_checker_catches_order_and_missing_courses():
    cat, st = lang_catalog(), lang_student("LNG 112")
    plan = plan_for_student(cat, st, SET, time_limit=10, workers=4)
    first = next(tp for tp in plan.terms if "LNG 112" in tp.course_ids)
    later = next(tp for tp in plan.terms if "LNG 153" in tp.course_ids)
    item = next(i for i in later.items if i.id == "LNG 153")
    later.items.remove(item)
    first.items.append(item)                                          # same semester as LNG 112
    problems = " | ".join(validate_plan(plan, cat, st, SET))
    assert "LNG 153 must come in a later semester than LNG 112" in problems
    for tp in plan.terms:
        tp.items = [i for i in tp.items if i.id != "LNG 201"]
    assert "foreign language (LNG 201 is not planned)" in " | ".join(validate_plan(plan, cat, st, SET))


def test_too_many_language_courses_for_the_time_left():
    cat = lang_catalog()
    st = lang_student("LNG 111", next_term=Term("fall", 2028))      # 4 courses, 2 semesters left
    plan = plan_for_student(cat, st, SET, time_limit=5, workers=4)
    assert not plan.ok and any("Foreign language: 4 courses" in e for e in plan.explanations)


def test_en_dash_prerequisite_marker():
    """Some departments write "P– SPA 101" with an en dash; it used to be dropped."""
    for text in ["P– SPA 101 or 111; or placement.", "P—SPA 153.", "P- CSC 111."]:
        body, _ = extract_requisite([text], PREREQ_START_RE)
        assert body and body[0] in "SC"


def test_form_language_fields():
    st = build_profile(**{**FORM, "language": "SPA", "language_placement": "SPA 102"})
    assert (st.language, st.language_placement) == ("SPA", "SPA 102")
    st = build_profile(**{**FORM, "language": "", "language_placement": "SPA 102"})
    assert st.language is None and st.language_placement is None


# ---------------------------------------------------------------- real data

@pytest.fixture(scope="module")
def real():
    from gradplanner.db.store import default_db_path, load_catalog
    if not default_db_path().exists():
        pytest.skip("run scripts/build_db.py first")
    return load_catalog()


def test_real_sequences_are_in_the_catalog(real):
    langs = load_settings(real).languages
    assert {"SPA", "FRH", "GER", "CHI", "LAT"} <= set(langs)
    assert langs["SPA"].courses[-1] == "SPA 202"                      # SPA 201 doesn't fulfill it (Bulletin)


def test_real_sam_spanish(real):
    st = load_profile("samples/sam_first_year_bs.yaml")
    plan = plan_for_student(real, st)
    assert plan.ok and validate_plan(plan, real, st) == []
    when = {cid: tp.term for tp in plan.terms for cid in tp.course_ids}
    assert when["SPA 201"] < when["SPA 202"] and "SPA 101" not in when
