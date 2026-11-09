"""Web app: form → profile helpers, and a headless run of the whole flow in Streamlit."""

import pytest

from gradplanner.planner.terms import Term
from gradplanner.ui.helpers import build_profile, profile_to_form, run_all, term_options
from test_planner import SETTINGS, catalog

FORM = dict(name="Test Student (fictional)", majors=["TST-BS"], minors=[], entry="Fall 2026", next_term="Fall 2026",
            graduation="Spring 2030", max_hours=17.0, summer=False, language_needed=1, completed=["TST 101"],
            ap_credit=["TST 102"], in_progress=[], outside=[{"name": "HST 101", "hours": 3, "counts_for": "DIV-I"},
                                                            {"name": "", "hours": None, "counts_for": ""}],
            interests=[], career="", career_text="")


def test_term_options():
    assert term_options(Term("fall", 2026), years=1) == ["Fall 2026", "Spring 2027"]


def test_build_profile_round_trip():
    st = build_profile(**FORM)
    assert [(c.course, c.source) for c in st.completed] == [("TST 101", "wfu"), ("TST 102", "ap")]
    assert len(st.outside_courses) == 1 and st.outside_courses[0].counts_for == "DIV-I"   # blank row skipped
    back = profile_to_form(st, catalog())
    assert back["completed"] == ["TST 101"] and back["ap_credit"] == ["TST 102"]
    assert back["outside"] == [{"name": "HST 101", "hours": 3.0, "counts_for": "DIV-I"}]


@pytest.mark.parametrize("change, message", [
    ({"graduation": "Spring 2026"}, "graduation"),
    ({"next_term": "Spring 2026"}, "first semester"),
    ({"in_progress": ["TST 101"]}, "both completed and in progress"),
    ({"majors": []}, "at least one major"),
])
def test_build_profile_rejects_bad_input(change, message):
    with pytest.raises(ValueError, match=message):
        build_profile(**{**FORM, **change})


# ---------------------------------------------------------------- real data, headless browser

def _app():
    from gradplanner.db.store import default_db_path
    if not default_db_path().exists():
        pytest.skip("run scripts/build_db.py first")
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file("../src/gradplanner/ui/app.py", default_timeout=180).run()


def _make_plan(at):
    next(b for b in at.main.button if b.label == "Make my plan").click().run()
    assert not at.exception


def test_app_full_flow_with_sample():
    at = _app()
    assert not at.exception
    at.sidebar.selectbox(key="start_from").select("Jordan Sample (fictional)")
    at.sidebar.button[0].click().run()
    _make_plan(at)
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Plan check"] == "Valid ✅" and metrics["Semesters"] == "4"
    assert sum(m.label == "Score" for m in at.metric) == 5          # career picks
    assert int(metrics["✅ done"]) > 0                               # audit
    assert any("Advisory only" in i.value for i in at.info)


def test_app_bad_input_and_partial_program():
    at = _app()
    at.sidebar.selectbox(key="f_graduation").select("Spring 2026")
    _make_plan(at)
    assert any("graduation" in e.value for e in at.error)
    at.sidebar.selectbox(key="f_graduation").select("Spring 2030")
    at.sidebar.multiselect(key="f_majors").set_value(["BIO-BS"]).run()
    if _support("BIO-BS") != "full":
        assert any("Biology" in w.value for w in at.warning)          # "advisor review" banner
    _make_plan(at)
    assert {m.label: m.value for m in at.metric}["Plan check"] == "Valid ✅"


def _support(pid):
    from gradplanner.db.store import load_catalog
    return load_catalog().programs[pid].support


def test_run_all_without_career():
    st = build_profile(**{**FORM, "outside": []})
    res = run_all(catalog(), st, time_limit=10, settings=SETTINGS)
    assert res.plan.ok and res.problems == [] and res.recommendations == []


def test_build_profile_abroad():
    st = build_profile(**{**FORM, "abroad": True, "abroad_terms": ["Spring 2029"], "abroad_program": "affiliate",
                          "abroad_location": " Madrid "})
    assert [(str(a.term), a.program, a.location) for a in st.abroad] == [("Spring 2029", "affiliate", "Madrid")]
    assert build_profile(**{**FORM, "abroad": False, "abroad_terms": ["Spring 2029"]}).abroad == []
    with pytest.raises(ValueError, match="study abroad"):
        build_profile(**{**FORM, "abroad": True, "abroad_terms": []})


def test_app_study_abroad():
    at = _app()
    at.sidebar.checkbox(key="f_abroad").check().run()
    assert at.session_state["f_abroad_terms"] == ["Spring 2029"]      # junior spring by default
    at.sidebar.text_input(key="f_abroad_location").input("London").run()
    _make_plan(at)
    assert {m.label: m.value for m in at.metric}["Plan check"] == "Valid ✅"
    assert any("Study abroad: London" in m.value for m in at.markdown)


def test_app_record_semester_and_replan():
    at = _app()
    at.sidebar.selectbox(key="start_from").select("Sam Example (fictional)")
    at.sidebar.button[0].click().run()
    _make_plan(at)
    boxes = [s for s in at.selectbox if s.key and s.key.startswith("outcome_")]
    failed = boxes[0].label.split(" – ")[0]
    boxes[0].select("failed")
    next(b for b in at.button if "and re-plan" in b.label).click().run()
    assert not at.exception
    assert at.session_state["f_next_term"] == "Spring 2027"           # the sidebar shows the updated profile
    assert failed not in at.session_state["f_completed"]
    assert any(m.value.startswith("🔁 " + failed) for m in at.markdown)
    assert {m.label: m.value for m in at.metric}["Plan check"] == "Valid ✅"
    assert not any("profile changed" in w.value for w in at.warning)
