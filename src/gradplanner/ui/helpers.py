"""Plain-Python helpers for the Streamlit app (kept separate so they can be unit-tested)."""

from __future__ import annotations

from dataclasses import dataclass, field

from gradplanner.audit import Audit, run_audit
from gradplanner.models import Catalog
from gradplanner.planner import StudentProfile, plan_for_student, validate_plan
from gradplanner.planner.plan import Plan
from gradplanner.planner.requirements import PlannerSettings
from gradplanner.planner.student import AbroadTerm, CompletedCourse, OutsideCourse
from gradplanner.planner.terms import Term
from gradplanner.recommender import Recommender, career_query
from gradplanner.recommender.tfidf import Recommendation, business_skip

SUPPORT_TEXT = {
    "full": "All requirements are planned automatically.",
    "partial": "Some requirements need an advisor; they show as 'advisor review' in the audit.",
    "none": "Requirements are written as paragraphs in the Bulletin; only the core curriculum is planned.",
}
STATUS_ICON = {"done": "✅", "in_progress": "🔄", "planned": "🗓️", "remaining": "⬜", "review": "❓"}
STATUS_TEXT = {"done": "done", "in_progress": "in progress", "planned": "planned",
               "remaining": "remaining", "review": "advisor review"}


def program_label(catalog: Catalog, pid: str) -> str:
    p = catalog.programs[pid]
    mark = {"full": "", "partial": " · some advisor review", "none": " · core only"}[p.support]
    return f"{p.name}{mark}"


def program_ids(catalog: Catalog, kind: str | None = None) -> list[str]:
    """Program ids alphabetical by name; only majors or only minors if `kind` is given."""
    return sorted((pid for pid, p in catalog.programs.items() if kind is None or p.type == kind),
                  key=lambda pid: catalog.programs[pid].name.lower())


def term_options(first: Term, years: int = 6) -> list[str]:
    """Fall and Spring terms for `years` school years starting at `first`."""
    out, t = [], first
    while len(out) < years * 2:
        out.append(str(t))
        t = t.next()
    return out


def course_label(catalog: Catalog, cid: str) -> str:
    c = catalog.courses.get(cid)
    return f"{cid} – {c.title}" if c else cid


def build_profile(*, name: str, majors: list[str], minors: list[str], entry: str, next_term: str, graduation: str,
                  max_hours: float, summer: bool, language_needed: int, completed: list[str],
                  ap_credit: list[str], in_progress: list[str], outside: list[dict],
                  interests: list[str], career: str | None, career_text: str, abroad: bool = False,
                  abroad_terms: list[str] = (), abroad_program: str = "wfu",
                  abroad_location: str = "") -> StudentProfile:
    """A profile from the sidebar form. Raises ValueError with a readable message on bad input."""
    if not majors:
        raise ValueError("Choose at least one major.")
    if abroad and not abroad_terms:
        raise ValueError("Choose the semester(s) you'll study abroad.")
    entry_t, next_t, grad_t = Term.parse(entry), Term.parse(next_term), Term.parse(graduation)
    if next_t < entry_t:
        raise ValueError("The first semester to plan can't be before your first semester at Wake Forest.")
    if grad_t < next_t:
        raise ValueError("The graduation semester can't be before the first semester to plan.")
    overlap = set(completed + ap_credit) & set(in_progress)
    if overlap:
        raise ValueError(f"Listed as both completed and in progress: {', '.join(sorted(overlap))}.")
    done = [CompletedCourse(c) for c in completed] + [CompletedCourse(c, "ap") for c in ap_credit
                                                       if c not in completed]
    rows = []
    for r in outside:
        nm = str(r.get("name") or "").strip()
        hours = r.get("hours")
        if not nm or hours in (None, "") or hours != hours:     # skip empty / NaN rows
            continue
        rows.append(OutsideCourse(nm, float(hours), r.get("counts_for") or None, r.get("source") or "wfu"))
    return StudentProfile(
        name=name.strip() or "Student (fictional)", program=majors[0],
        other_programs=list(majors[1:]) + list(minors), entry_term=entry_t, next_term=next_t,
        graduation_term=grad_t, max_hours_per_term=max_hours, include_summer=summer,
        language_courses_needed=language_needed, completed=done, in_progress=list(in_progress),
        outside_courses=rows, interests=list(interests), career=career or None, career_text=career_text.strip(),
        abroad=[AbroadTerm(Term.parse(t), abroad_program, abroad_location.strip())
                for t in (abroad_terms if abroad else [])])


def profile_to_form(st: StudentProfile, catalog: Catalog) -> dict:
    """Form defaults from a loaded (sample) profile."""
    kinds = {p: catalog.programs[p].type if p in catalog.programs else "major" for p in st.programs}
    return {
        "name": st.name, "majors": [p for p in st.programs if kinds[p] == "major"],
        "minors": [p for p in st.programs if kinds[p] == "minor"], "entry": str(st.entry_term), "next_term": str(st.next_term),
        "graduation": str(st.graduation_term), "max_hours": float(st.max_hours_per_term),
        "summer": st.include_summer, "language_needed": st.language_courses_needed,
        "completed": [c.course for c in st.completed if c.source == "wfu"],
        "ap_credit": [c.course for c in st.completed if c.source != "wfu"],
        "in_progress": list(st.in_progress),
        "outside": [{"name": o.name, "hours": o.hours, "counts_for": o.counts_for or ""}
                    for o in st.outside_courses],
        "interests": list(st.interests), "career": st.career or "", "career_text": st.career_text,
        "abroad": bool(st.abroad), "abroad_terms": [str(a.term) for a in st.abroad],
        "abroad_program": st.abroad[0].program if st.abroad else "wfu",
        "abroad_location": st.abroad[0].location if st.abroad else "",
    }


@dataclass
class Result:
    student: StudentProfile
    plan: Plan
    problems: list[str]                       # independent checker findings (empty = valid)
    audit: Audit
    recommendations: list[Recommendation] = field(default_factory=list)
    goal_label: str = ""


def run_all(catalog: Catalog, student: StudentProfile, recommender: Recommender | None = None,
            time_limit: float = 10, top_n: int = 5, settings: PlannerSettings | None = None) -> Result:
    """Recommend, plan, check, and audit: the whole flow behind the 'Make my plan' button."""
    recs, relevance, label = [], None, ""
    query, label = career_query(student.career, student.career_text)
    if query:
        rec = recommender or Recommender(catalog)
        recs = rec.recommend(query, student.programs, student.completed_ids, top_n, label,
                             skip=business_skip(catalog, student))
        relevance = rec.scores(query)
    plan = plan_for_student(catalog, student, settings, time_limit=time_limit, relevance=relevance)
    problems = validate_plan(plan, catalog, student, settings) if plan.ok else []
    audit = run_audit(catalog, student, plan if plan.ok else None, settings)
    return Result(student, plan, problems, audit, recs, label)
