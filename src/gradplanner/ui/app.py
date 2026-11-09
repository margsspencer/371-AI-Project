"""WFU Graduation Calendar Planner: the web app.

Run with:
    streamlit run src/gradplanner/ui/app.py

Flow: student profile (sidebar) → "Make my plan" → plan, career picks, and degree audit tabs.
Fictional students only; advisory only.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from gradplanner import config
from gradplanner.db.store import default_db_path, load_catalog
from gradplanner.planner import load_profile
from gradplanner.planner.requirements import load_settings, program_problems
from gradplanner.planner.terms import Term
from gradplanner.recommender import Recommender, load_careers
from gradplanner.recommender.tfidf import WEAK_SCORE
from gradplanner.planner.replan import OUTCOMES, item_key
from gradplanner.ui.helpers import (CHANGE_ICON, STATUS_ICON, STATUS_TEXT, SUPPORT_TEXT, build_profile, course_label,
                                    profile_to_form, program_ids, program_label, record_and_replan, run_all,
                                    term_options)

SAMPLES_DIR = config.PROJECT_ROOT / "samples"
ADVISORY = ("**Advisory only.** This planner reads the 2026-27 Undergraduate Bulletin. Confirm every plan "
            "with your academic advisor and the current Bulletin. Use fictional student data only.")
PLAN_STATUS = {"optimal": "Best plan found", "feasible": "Good plan (time ran out before proving it's the best)"}

st.set_page_config(page_title="WFU Graduation Planner", page_icon="🎓", layout="wide")


@st.cache_resource(show_spinner="Loading the Bulletin database...")
def catalog():
    return load_catalog()


@st.cache_resource(show_spinner="Reading course descriptions...")
def recommender():
    return Recommender(catalog())


# ---------------------------------------------------------------- form state

BLANK = {"name": "New Student (fictional)", "majors": ["CSC-BS"], "minors": [], "entry": "Fall 2026", "next_term": "Fall 2026",
         "graduation": "Spring 2030", "max_hours": 17.0, "summer": False, "language_needed": 1,
         "completed": [], "ap_credit": [], "in_progress": [], "outside": [], "interests": [],
         "career": "", "career_text": "", "abroad": False, "abroad_terms": [], "abroad_program": "wfu",
         "abroad_location": ""}
ABROAD_PROGRAMS = {"wfu": "Wake Forest program (e.g. Worrell House, Casa Artom, Flow House)",
                   "affiliate": "Affiliate (non-Wake Forest) program: transfer credit"}


def load_form(values: dict, keep_result: bool = False) -> None:
    for k, v in values.items():
        st.session_state[f"f_{k}"] = v
    st.session_state.outside_df = pd.DataFrame(values["outside"] or [], columns=["name", "hours", "counts_for"])
    st.session_state.pop("outside_editor", None)      # old table edits would apply to the new rows
    if not keep_result:
        st.session_state.pop("result", None)


def sample_files() -> dict[str, str]:
    return {load_profile(p).name: str(p) for p in sorted(SAMPLES_DIR.glob("*.yaml"))}


def sidebar(cat) -> dict | None:
    sb = st.sidebar
    sb.title("🎓 Student profile")
    samples = sample_files()
    choice = sb.selectbox("Start from", ["Blank profile"] + list(samples), key="start_from")
    if "pending_form" in st.session_state:          # after re-planning: show the updated profile
        load_form(st.session_state.pop("pending_form"), keep_result=True)
    if sb.button("Load", use_container_width=True) or "f_name" not in st.session_state:
        load_form(BLANK if choice == "Blank profile" else profile_to_form(load_profile(samples[choice]), cat))

    courses = sorted(cat.courses)
    careers = load_careers()
    terms = term_options(Term("fall", 2022), years=9)

    sb.text_input("Name (fictional)", key="f_name")
    plabel = lambda p: program_label(cat, p)       # noqa: E731
    sb.multiselect("Major(s)", program_ids(cat, "major"), format_func=plabel, key="f_majors", max_selections=2,
                   help="Up to two majors. The first one is your main major.")
    sb.multiselect("Minor(s)", program_ids(cat, "minor"), format_func=plabel, key="f_minors", max_selections=2,
                   help="Optional. A course may count toward a major and a minor at the same time.")
    c1, c2 = sb.columns(2)
    c1.selectbox("First semester at WFU", terms, key="f_entry")
    c2.selectbox("First semester to plan", terms, key="f_next_term")
    sb.selectbox("Graduate by", terms, key="f_graduation")
    c1, c2 = sb.columns(2)
    c1.number_input("Max hours / semester", 12.0, 17.0, step=1.0, key="f_max_hours",
                    help="The Bulletin allows up to 17 hours without special permission.")
    c2.number_input("Language courses left", 0, 4, key="f_language_needed",
                    help="Courses still needed to finish one 200-level foreign language course.")
    sb.checkbox("Allow summer courses", key="f_summer")

    sb.subheader("Courses")
    fmt = lambda c: course_label(cat, c)          # noqa: E731
    sb.multiselect("Completed at Wake Forest", courses, format_func=fmt, key="f_completed")
    sb.multiselect("AP / transfer credit", courses, format_func=fmt, key="f_ap_credit",
                   help="AP and transfer credit can't count for divisional or 21st-century core requirements.")
    sb.multiselect("In progress this semester", courses, format_func=fmt, key="f_in_progress",
                   help="The plan assumes you pass these.")
    sb.caption("Other finished courses (core classes, electives) and what they count for:")
    core_ids = [""] + [c.id for c in cat.core]
    outside = sb.data_editor(
        st.session_state.outside_df, num_rows="dynamic", use_container_width=True, key="outside_editor",
        column_config={"name": st.column_config.TextColumn("Course"),
                       "hours": st.column_config.NumberColumn("Hours", min_value=0, max_value=20, step=0.5),
                       "counts_for": st.column_config.SelectboxColumn("Counts for", options=core_ids)})
    sb.multiselect("Electives I definitely want", courses, format_func=fmt, key="f_interests")

    sb.subheader("Study abroad")
    if sb.checkbox("I plan to study abroad", key="f_abroad",
                   help="Most Wake Forest students go during junior year."):
        plan_terms = [t for t in terms if Term.parse(st.session_state.f_next_term)
                      <= Term.parse(t) <= Term.parse(st.session_state.f_graduation)]
        if not st.session_state.get("f_abroad_terms"):
            junior_spring = f"Spring {Term.parse(st.session_state.f_entry).year + 3}"
            st.session_state.f_abroad_terms = [junior_spring] if junior_spring in plan_terms else plan_terms[:1]
        st.session_state.f_abroad_terms = [t for t in st.session_state.f_abroad_terms if t in plan_terms]
        sb.multiselect("Semester(s) abroad", plan_terms, key="f_abroad_terms", max_selections=2)
        sb.radio("Program", list(ABROAD_PROGRAMS), format_func=ABROAD_PROGRAMS.get, key="f_abroad_program")
        sb.text_input("Where (optional)", key="f_abroad_location", placeholder="e.g. London")
        sb.caption("Abroad courses are planned toward divisionals, the language requirement, and up to 6 hours "
                   "of your major/minor (department limits apply). Get courses approved before you go.")

    sb.subheader("Career goal")
    sb.selectbox("Career", [""] + list(careers), key="f_career",
                 format_func=lambda c: careers[c].label if c else "(none)")
    sb.text_input("In your own words (optional)", key="f_career_text", placeholder="e.g. AI for healthcare")

    form = {k[2:]: st.session_state[k] for k in st.session_state if k.startswith("f_")}
    form["outside"] = outside.to_dict("records")
    return form


# ---------------------------------------------------------------- result views

def show_plan(cat, res) -> None:
    plan = res.plan
    if not plan.ok:
        st.error("No plan fits these settings.")
        for why in plan.explanations:
            st.markdown(f"- {why}")
        st.info("Try a later graduation semester, a higher hour limit, or allowing summer courses.")
        return
    total = sum(tp.hours for tp in plan.terms)
    c1, c2, c3 = st.columns(3)
    c1.metric("Semesters", len(plan.terms))
    c2.metric("Planned hours", f"{total:g}")
    c3.metric("Plan check", "Valid ✅" if not res.problems else f"{len(res.problems)} problem(s)")
    st.caption(PLAN_STATUS.get(plan.status, plan.status) + ". The plan check is a separate program that "
               "re-checks prerequisites, hour limits, offerings, and requirements.")
    for p in res.problems:
        st.error(p)
    if res.diff:
        show_diff(res)

    picks = {r.course_id for r in res.recommendations}
    for i in range(0, len(plan.terms), 2):
        cols = st.columns(2)
        for col, tp in zip(cols, plan.terms[i:i + 2]):
            with col.container(border=True):
                where = f" · 🌍 Study abroad: {tp.abroad}" if tp.abroad else ""
                st.markdown(f"**{tp.term}** · {tp.hours:g} hours{where}")
                rows = [{"Course": ("⭐ " if it.id in picks else "") + (it.id if it.kind == "course" else
                                                                         "🌍" if it.kind == "abroad" else ""),
                         "Title": it.title, "Hours": it.hours,
                         "Counts toward": ", ".join(plan.labels.get(k, k) for k in it.counts_toward)}
                        for it in tp.items]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if picks:
        st.caption("⭐ = recommended for your career goal")
    if plan.notes:
        with st.expander(f"Things to check with your advisor ({len(plan.notes)})"):
            for n in plan.notes:
                st.markdown(f"- {n}")
    record_form(cat, res)


def show_diff(res) -> None:
    d = res.diff
    with st.container(border=True):
        st.markdown(f"**🔄 What changed · {res.diff_title}**")
        st.markdown(d.summary[0].upper() + d.summary[1:])
        main = [c for c in d.changes if "elective" not in c.what.lower() or c.kind in ("done", "failed", "retake")]
        electives = [c for c in d.changes if c not in main]
        for c in main:
            st.markdown(f"{CHANGE_ICON[c.kind]} {c.sentence}")
        if electives:
            with st.expander(f"Free-elective slots that moved ({len(electives)})"):
                for c in electives:
                    st.markdown(f"{CHANGE_ICON[c.kind]} {c.sentence}")
        if not d.changes:
            st.markdown("Nothing changed.")


def record_form(cat, res) -> None:
    """Mark how the first planned semester went, then re-plan from the next one."""
    first = res.plan.terms[0]
    with st.expander(f"📝 Finished {first.term}? Record how it went and re-plan"):
        st.caption("Everything is marked passed unless you change it. Failed or dropped courses that are still "
                   "needed are planned again; the rest of the plan stays as it is where possible.")
        with st.form("record_term"):
            outcomes = {}
            for i, item in enumerate(first.items):
                label = f"{item.id} – {item.title}" if item.kind == "course" else item.title
                outcomes[item_key(item, i)] = st.selectbox(label, OUTCOMES, key=f"outcome_{i}_{item.id}")
            submitted = st.form_submit_button(f"Record {first.term} and re-plan", type="primary")
        if submitted:
            outcomes = {k: v for k, v in outcomes.items() if v != "passed"}
            with st.spinner("Re-planning..."):
                new = record_and_replan(cat, res, outcomes, recommender())
            st.session_state.result = new
            st.session_state.pending_form = profile_to_form(new.student, cat)
            st.session_state.replanned = True
            st.rerun()


def show_recommendations(cat, res) -> None:
    if not res.recommendations:
        st.info("Pick a career or describe your goal in the sidebar to get course suggestions.")
        return
    st.markdown(f"Courses that match **{res.goal_label}**. The score (0-100) is how strongly the course "
                "description matches your goal's words.")
    if res.recommendations[0].score < WEAK_SCORE:
        st.warning("These are weak matches: this year's catalog has few courses on this topic. "
                   "Talk to your advisor.")
    for r in res.recommendations:
        where = res.plan.term_of(r.course_id) if res.plan.ok else None
        with st.container(border=True):
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"**{r.course_id} – {r.title}**")
            c2.metric("Score", r.score, label_visibility="collapsed")
            st.markdown(r.reason)
            bits = [f"In your plan: **{where}**" if where else "Not in your plan (no room or not needed)"]
            bits.append("Counts toward: " + "; ".join(r.counts_toward) if r.counts_toward else "Free elective")
            st.caption(" · ".join(bits))


def show_audit(res) -> None:
    audit = res.audit
    cols = st.columns(5)
    for col, s in zip(cols, STATUS_ICON):
        col.metric(f"{STATUS_ICON[s]} {STATUS_TEXT[s]}", audit.count(s))
    st.caption("Counts completed and in-progress courses" + (" plus the plan." if res.plan.ok else "."))
    sections: dict[str, list] = {}
    for line in audit.lines:
        sections.setdefault(line.section, []).append(line)
    for name, lines in sections.items():
        st.subheader(name)
        rows = [{"": STATUS_ICON[l.status], "Requirement": l.label, "Status": STATUS_TEXT[l.status],
                 "Details": l.detail} for l in lines]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    for n in audit.notes:
        st.caption(f"Note: {n}")


def show_about(cat) -> None:
    progs = cat.programs.values()
    st.markdown(f"Data: Wake Forest Undergraduate Bulletin **{cat.bulletin_year}** "
                f"({len(cat.courses)} courses, {len(cat.programs)} majors and minors).")
    counts = {s: sum(p.support == s for p in progs) for s in ("full", "partial", "none")}
    for s, n in counts.items():
        st.markdown(f"- **{n}** programs: {SUPPORT_TEXT[s]}")
    st.markdown("- Students who entered before 2026 may follow an older Bulletin; this planner uses "
                "the 2026-27 rules for everyone.")
    st.markdown("- Certificates, concentrations, and self-designed majors aren't included.")


# ---------------------------------------------------------------- page

def main() -> None:
    if not default_db_path().exists():
        st.error("No database yet. Run `python scripts/build_db.py` first.")
        st.stop()
    cat = catalog()
    form = sidebar(cat)

    st.title("WFU Graduation Calendar Planner")
    st.info(ADVISORY)
    for pid in form["majors"] + form["minors"]:
        prog = cat.programs[pid]
        if prog.support != "full":
            st.warning(f"**{prog.name}:** {SUPPORT_TEXT[prog.support]}")

    res = st.session_state.get("result")
    keep = False
    if res is not None and res.plan.ok:
        keep = st.checkbox("Keep my previous plan where possible", value=True,
                           help="The new plan changes only what it has to, and lists what changed.")
    if st.button("Make my plan", type="primary"):
        try:
            student = build_profile(**form)
            conflicts = program_problems(cat, student, load_settings(cat))
            if conflicts:
                raise ValueError(" ".join(conflicts))
        except ValueError as exc:
            st.error(str(exc))
        else:
            with st.spinner("Planning your semesters (up to about 10 seconds)..."):
                st.session_state.result = run_all(cat, student, recommender(),
                                                  previous=res.plan if keep and res else None)
                st.session_state.result_form = repr(form)

    res = st.session_state.get("result")
    if res is None:
        st.markdown("Fill in the profile on the left (or load a sample), then press **Make my plan**.")
        show_about(cat)
        return
    if st.session_state.pop("replanned", False):
        st.session_state.result_form = repr(form)          # the sidebar now shows the updated profile
    if st.session_state.get("result_form") != repr(form):
        st.warning("The profile changed since this plan was made. Press **Make my plan** to update it.")
    st.markdown(f"#### {res.student.name} · " + " + ".join(cat.programs[p].name for p in res.student.programs))
    tab_plan, tab_recs, tab_audit, tab_about = st.tabs(["📅 Plan", "⭐ Career picks", "✅ Degree audit", "ℹ️ About"])
    with tab_plan:
        show_plan(cat, res)
    with tab_recs:
        show_recommendations(cat, res)
    with tab_audit:
        show_audit(res)
    with tab_about:
        show_about(cat)


main()
