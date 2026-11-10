"""Re-planning (milestone 7): record a semester, plan again with stability, and diff the plans."""

import pytest

from gradplanner.planner import load_profile, plan_for_student, validate_plan
from gradplanner.planner.plan import Plan, PlanItem, TermPlan
from gradplanner.planner.replan import diff_plans, item_key, record_term
from gradplanner.planner.student import AbroadTerm, CompletedCourse, save_profile
from gradplanner.planner.terms import Term
from test_planner import SETTINGS, catalog, solve, student

F26, S27, F27, S28 = Term("fall", 2026), Term("spring", 2027), Term("fall", 2027), Term("spring", 2028)


def term_with(plan, cid):
    return next(tp for tp in plan.terms if cid in tp.course_ids)


def test_record_term_passed_failed_and_slots():
    cat, st, plan = solve()
    first = plan.terms[0]
    assert "TST 101" in first.course_ids
    after = record_term(st, plan, {"TST 101": "failed"})
    assert after.next_term == S27
    done = {c.course for c in after.completed}
    assert "TST 101" not in done and set(first.course_ids) - {"TST 101"} <= done
    slots = [i for i in first.items if i.kind != "course"]
    assert len(after.outside_courses) == len(slots)                 # passed slots become finished credit
    assert st.completed == []                                       # the original profile is unchanged
    with pytest.raises(ValueError):
        record_term(st, plan, {"TST 101": "maybe"})


def test_failed_course_with_no_time_left_is_explained():
    cat, st, plan = solve()                                          # graduating Spring 2028
    after = record_term(st, plan, {"TST 101": "failed"})
    new = plan_for_student(cat, after, SETTINGS, time_limit=10, workers=4, previous=plan)
    assert not new.ok and "TST 301" in new.explanations[0]         # 101 -> 201 -> 301 (fall only) no longer fits


def test_failed_required_course_is_planned_again_and_chain_moves():
    cat, st, plan = solve(student(graduation_term=Term("spring", 2029)))
    after = record_term(st, plan, {"TST 101": "failed"})
    new = plan_for_student(cat, after, SETTINGS, time_limit=10, workers=4, previous=plan)
    assert new.ok and validate_plan(new, cat, after, SETTINGS) == []
    assert term_with(new, "TST 101").term == S27
    assert term_with(new, "TST 201").term > S27                     # its follow-on course waits a semester
    d = diff_plans(plan, new, {"TST 101": "failed"}, recorded=F26)
    retake = d.of("retake")
    assert [c.what.split()[0] + " " + c.what.split()[1] for c in retake] == ["TST 101"]
    assert "failed in Fall 2026; planned again in Spring 2027" in retake[0].sentence
    assert d.of("done")                                             # the other first-semester courses
    assert "moved" in d.summary


def test_stability_keeps_unaffected_courses():
    """Re-planning with nothing changed returns the same semesters for every course."""
    cat, st, plan = solve()
    again = plan_for_student(cat, st, SETTINGS, time_limit=10, workers=4, previous=plan)
    d = diff_plans(plan, again)
    assert not d.of("moved", "added", "removed"), [c.sentence for c in d.changes]
    assert d.unchanged == sum(len(tp.course_ids) for tp in plan.terms)


def test_diff_sentences_and_slots():
    def plan_of(*terms):
        return Plan("X", "TST-BS", "optimal", [TermPlan(t, items) for t, items in terms])
    c = lambda cid: PlanItem(cid, "title", 3, "course")                      # noqa: E731
    slot = lambda: PlanItem("DIV-I", "Division I (choose a course)", 3, "core_slot", ["DIV-I"])  # noqa: E731
    old = plan_of((F26, [c("A 1"), c("B 1")]), (S27, [c("C 1"), slot()]), (F27, [c("D 1")]))
    new = plan_of((S27, [c("C 1")]), (F27, [c("D 1"), c("E 1"), slot()]))
    d = diff_plans(old, new, {"B 1": "dropped"}, recorded=F26)
    got = {c.kind: c.sentence for c in d.changes}
    assert got["done"] == "A 1 title: done in Fall 2026."
    assert got["failed"].startswith("B 1 title: dropped in Fall 2026; not needed again")
    assert got["added"] == "E 1 title: added in Fall 2027."
    assert got["moved"] == "Division I (choose a course): moved from Spring 2027 to Fall 2027."
    assert d.unchanged == 2 and "still graduating Fall 2027" in d.summary
    assert item_key(slot(), 3) == "DIV-I#3"


def test_dropped_slot_is_not_counted():
    cat, st, plan = solve()
    first = plan.terms[0]
    keys = {item_key(i, n): i for n, i in enumerate(first.items) if i.kind != "course"}
    if not keys:
        pytest.skip("no slot in the first semester of this plan")
    key = next(iter(keys))
    after = record_term(st, plan, {key: "dropped"})
    assert len(after.outside_courses) == len(keys) - 1


def test_profile_save_round_trip(tmp_path):
    st = student(completed=[CompletedCourse("TST 101", "ap")], other_programs=[],
                 abroad=[AbroadTerm(S28, "affiliate", "Madrid")])
    path = tmp_path / "p.yaml"
    save_profile(st, path)
    back = load_profile(path)
    assert back == st


# ---------------------------------------------------------------- real data

@pytest.fixture(scope="module")
def real():
    from gradplanner.db.store import default_db_path, load_catalog
    if not default_db_path().exists():
        pytest.skip("run scripts/build_db.py first")
    c = load_catalog()
    if "FIN-BS" not in c.programs:
        pytest.skip("run scripts/scrape.py --all and scripts/build_db.py for all departments")
    return c


def test_real_failed_course_replan(real):
    st = load_profile("samples/sam_first_year_bs.yaml")
    plan = plan_for_student(real, st)
    fail = plan.terms[0].course_ids[0]          # whichever course the plan starts with
    after = record_term(st, plan, {fail: "failed"})
    new = plan_for_student(real, after, previous=plan)
    assert new.ok and validate_plan(new, real, after) == []
    assert new.term_of(fail) is not None and new.terms[0].term == Term("spring", 2027)
    d = diff_plans(plan, new, {fail: "failed"}, recorded=plan.terms[0].term)
    assert d.unchanged > 0 and d.of("retake")


def test_real_abroad_credit_is_kept_after_the_semester(real):
    st = load_profile("samples/riley_first_year_finance.yaml")
    st.abroad = [AbroadTerm(Term("fall", 2026), "wfu", "Test")]   # abroad first, to record it right away
    plan = plan_for_student(real, st)
    assert plan.ok
    after = record_term(st, plan)
    assert after.abroad == [] and len(after.outside_courses) == len(plan.terms[0].items)
    new = plan_for_student(real, after, previous=plan)
    assert new.ok and validate_plan(new, real, after) == []
