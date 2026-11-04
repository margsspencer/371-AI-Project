"""Degree audit on the made-up catalog from test_planner, plus a hand-checked real-data sample."""

import pytest

from gradplanner.audit import run_audit
from gradplanner.parser.core import CoreCategory
from gradplanner.parser.programs import RequirementItem
from gradplanner.planner import plan_for_student, validate_plan
from gradplanner.planner.student import CompletedCourse, OutsideCourse
from test_planner import SETTINGS, catalog, course, student


def status(audit, label):
    return next(line for line in audit.lines if line.label == label).status


def line(audit, label):
    return next(ln for ln in audit.lines if ln.label == label)


def test_done_in_progress_planned_remaining():
    cat = catalog()
    st = student(completed=[CompletedCourse("TST 101")], in_progress=["TST 201"])
    audit = run_audit(cat, st, settings=SETTINGS)
    assert status(audit, "TST 101") == "done"
    assert status(audit, "TST 201") == "in_progress"
    assert status(audit, "TST 301") == "remaining"
    assert audit.hours == {"done": 3, "in_progress": 3, "planned": 0}


def test_audit_of_a_planned_schedule_has_nothing_remaining():
    cat = catalog()
    st = student(completed=[CompletedCourse("TST 101")], in_progress=["TST 201"])
    plan = plan_for_student(cat, st, SETTINGS, time_limit=10, workers=4)
    assert plan.ok and validate_plan(plan, cat, st, SETTINGS) == []
    assert plan.term_of("TST 201") is None              # in progress: not planned again
    assert any("Assumes you pass" in n for n in plan.notes)
    audit = run_audit(cat, st, plan, SETTINGS)
    assert audit.count("remaining") == 0, [(ln.label, ln.detail) for ln in audit.lines if ln.status == "remaining"]
    assert status(audit, "TST 101") == "done"
    assert status(audit, "TST 301") == "planned"
    assert status(audit, "36 total hours") == "planned"


def test_hours_rules_strictest_first_and_no_double_count():
    cat = catalog()
    group = cat.programs["TST-BS"].groups[0]
    group.items.append(RequirementItem("hours", "3 h of TST 200+", hours=3, subject="TST", min_level=200))
    st = student(completed=[CompletedCourse("TST 101"), CompletedCourse("TST 201"), CompletedCourse("TST 302")])
    audit = run_audit(cat, st, settings=SETTINGS)
    # TST 302 fills the 300+ rule; TST 201 is listed in the program, so nothing is left for 200+.
    assert line(audit, "3 h of TST 300+").detail.startswith("TST 302")
    assert status(audit, "3 h of TST 200+") == "remaining"
    cat.courses["TST 250"] = course("TST 250", "TST 101")
    st.completed.append(CompletedCourse("TST 250"))
    audit = run_audit(cat, st, settings=SETTINGS)
    assert line(audit, "3 h of TST 200+").detail.startswith("TST 250")


def test_division_rules_departments_and_ap():
    cat = catalog()
    cat.courses["TST 102"] = course("TST 102")
    st = student(completed=[CompletedCourse("TST 101"), CompletedCourse("TST 102")])
    audit = run_audit(cat, st, settings=SETTINGS)
    assert line(audit, "V. Math and Natural Sciences").detail == "1 of 2"     # same department twice
    st.completed.append(CompletedCourse("MTH 101", "ap"))
    assert status(run_audit(cat, st, settings=SETTINGS), "V. Math and Natural Sciences") == "remaining"
    st.completed[-1] = CompletedCourse("MTH 101")
    assert status(run_audit(cat, st, settings=SETTINGS), "V. Math and Natural Sciences") == "done"


def test_one_course_counts_for_one_21cs_category():
    cat = catalog()
    cat.core += [CoreCategory("21CS-EI", "21CS", "Ethical Inquiry", 1, tag="EI", placeholder=True),
                 CoreCategory("21CS-POR", "21CS", "POR", 1, tag="POR", placeholder=True)]
    cat.courses["TST 150"] = course("TST 150", tags=("EI", "POR"))
    audit = run_audit(cat, student(completed=[CompletedCourse("TST 150")]), settings=SETTINGS)
    assert sorted([status(audit, "Ethical Inquiry"), status(audit, "POR")]) == ["done", "remaining"]


def test_outside_courses_and_free_text():
    cat = catalog()
    cat.programs["TST-BS"].groups[0].items.append(RequirementItem("free_text", "Portfolio review"))
    st = student(outside_courses=[OutsideCourse("FYS 100", 3, "BASIC-FYS")])
    audit = run_audit(cat, st, settings=SETTINGS)
    assert status(audit, "First-year seminar") == "done"
    assert status(audit, "Portfolio review") == "review"


# ---------------------------------------------------------------- hand-checked real sample

# Worked out by hand from the 2026-27 Bulletin for samples/jordan_junior_bs.yaml (no plan).
JORDAN_EXPECTED = {
    "CSC 111": "done", "CSC 112": "done", "CSC 201": "done",
    "CSC 231": "remaining", "CSC 250": "remaining", "CSC 251": "remaining", "CSC 270": "remaining",
    "CSC 280": "remaining", "CSC 301": "remaining", "CSC 399": "remaining",
    "Select at least 12 hours in computer science courses at the 300-level or higher": "remaining",
    "MTH 112": "done", "MTH 117": "done", "MTH 121 or MTH 205": "remaining",
    "First-year seminar": "done", "Writing": "done", "Foreign language (one 200-level course)": "done",
    "Health and exercise science": "done",
    "I: Humanities": "done", "II: Literatures": "done", "III: Fine Arts": "remaining",
    "IV. Social Sciences": "done", "V. Math and Natural Sciences": "done",
    "Power, Oppression, and Resistance (POR)": "remaining", "Study in World Cultures (SWC)": "remaining",
    "Ethical Inquiry (EI)": "remaining", "Quantitative Data Analysis (QDA)": "done",
    "Quantitative Reasoning (QR)": "done", "120 total hours": "remaining",
}


def test_hand_checked_jordan_audit():
    from gradplanner import config
    from gradplanner.db.store import default_db_path, load_catalog
    from gradplanner.planner import load_profile

    if not default_db_path().exists():
        pytest.skip("run scripts/build_db.py first")
    audit = run_audit(load_catalog(), load_profile(config.PROJECT_ROOT / "samples" / "jordan_junior_bs.yaml"))
    assert {ln.label: ln.status for ln in audit.lines} == JORDAN_EXPECTED
    # AP credit (CSC 111) counts for the major but not for Division V.
    assert "CSC 111" not in line(audit, "V. Math and Natural Sciences").detail
    assert audit.hours["done"] == 61
