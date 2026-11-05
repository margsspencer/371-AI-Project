"""Career-based course recommender (TF-IDF + cosine similarity).

How it works:
1. Every course becomes a vector of word weights (TF-IDF) built from its
   Bulletin title and description. Words that are frequent in one course but
   rare across the catalog ("cryptography") weigh more than common ones
   ("concepts").
2. The career query (the student's own words + the pick-list keywords from
   careers.yaml) becomes a vector the same way.
3. Score = cosine similarity between the two, shown as 0-100.
4. The reason is a template: the words that contributed most, plus the
   description sentence that contains the most of them.

Course ids only ever come from the catalog, so the recommender can't invent
courses. It only ranks courses; the planner decides what fits.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

from gradplanner.models import Catalog, Course
from gradplanner.planner.requirements import (
    admission_courses, business_reason, fits_hours_rule, is_business_student, load_settings, program_needs,
    schedulable_reason,
)
from gradplanner.planner.student import StudentProfile

CAREERS_FILE = Path(__file__).with_name("careers.yaml")
WEAK_SCORE = 15          # below this, say the catalog has little on the topic

# Words that appear in many descriptions without saying what a course is about.
CATALOG_STOP_WORDS = {
    "course", "courses", "student", "students", "topic", "topics", "include", "includes", "including",
    "introduction", "introductory", "study", "studies", "concept", "concepts", "covered", "cover", "coverage",
    "credit", "hour", "hours", "prerequisite", "override", "required", "offered", "semester", "permission",
    "instructor", "department", "major", "minor", "level", "use", "used", "using", "learn", "basic",
    "fundamental", "fundamentals", "principle", "principles", "advanced", "selected", "various", "may",
    "csc", "mth", "sta", "bio", "phy", "lab", "laboratory", "also", "well", "emphasis",
}
_STOP = ENGLISH_STOP_WORDS | CATALOG_STOP_WORDS


def normalize(word: str) -> str:
    """Tiny stemmer so 'networks'/'network' and 'statistical'/'statistics' match."""
    for suffix, keep in (("ies", "y"), ("ical", "ic"), ("ics", "ic"), ("ing", ""), ("es", "e"), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4 and not word.endswith("ss"):
            return word[: len(word) - len(suffix)] + keep
    return word


def tokens(text: str) -> list[tuple[str, str]]:
    """(term, original words) pairs: single words, plus two-word phrases of words that sit
    next to each other in the text ("neural networks"), never across a comma or a dropped word."""
    out, prev = [], None
    for w in re.findall(r"[a-z]+|[^a-z\s]", text.lower()):
        if not w.isalpha() or len(w) <= 2 or w in _STOP:
            prev = None
            continue
        stem = normalize(w)
        out.append((stem, w))
        if prev and prev[0] != stem:
            out.append((f"{prev[0]} {stem}", f"{prev[1]} {w}"))
        prev = (stem, w)
    return out


def analyze(text: str) -> list[str]:
    return [term for term, _ in tokens(text)]


def course_text(course: Course) -> str:
    """Title (twice, it's the best summary) + description without the prerequisite sentence."""
    desc = re.split(r"\bP-\s", course.description)[0]
    return f"{course.title}. {course.title}. {desc}"


@dataclass
class Career:
    id: str
    label: str
    keywords: str


def load_careers(path: Path = CAREERS_FILE) -> dict[str, Career]:
    data = yaml.safe_load(path.read_text()) or {}
    return {cid: Career(cid, d["label"], d["keywords"]) for cid, d in data.items()}


@dataclass
class Recommendation:
    course_id: str
    title: str
    score: int                       # 0-100
    matched: list[str]               # words that contributed most
    quote: str                       # description sentence with the most matched words
    counts_toward: list[str] = field(default_factory=list)   # program rules it could fill
    reason: str = ""


class Recommender:
    """Fit once on a catalog, then score any query."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.ids = list(catalog.courses)
        self.vectorizer = TfidfVectorizer(analyzer=analyze, sublinear_tf=True)
        self.matrix = self.vectorizer.fit_transform([course_text(catalog.courses[c]) for c in self.ids])
        self.terms = self.vectorizer.get_feature_names_out()
        # Most common original spelling of each term, for readable explanations.
        forms: dict[str, Counter] = defaultdict(Counter)
        for cid in self.ids:
            for term, original in tokens(course_text(catalog.courses[cid])):
                forms[term][original] += 1
        self.display = {t: c.most_common(1)[0][0] for t, c in forms.items()}

    def scores(self, query: str) -> dict[str, int]:
        """{course_id: 0-100} for every course in the catalog."""
        q = self.vectorizer.transform([query])
        sims = (self.matrix @ q.T).toarray().ravel()
        return {cid: int(round(100 * s)) for cid, s in zip(self.ids, sims)}

    def _explain(self, cid: str, query_vec) -> tuple[list[str], str]:
        row = self.matrix[self.ids.index(cid)]
        contrib = row.multiply(query_vec).toarray().ravel()
        top = [self.terms[i] for i in np.argsort(-contrib)[:6] if contrib[i] > 0]
        words = [t for t in top if " " not in t]
        phrases = [t for t in top if " " in t]
        matched = (phrases + [w for w in words if not any(w in p.split() for p in phrases)])[:3]
        matched = [self.display.get(t, t) for t in matched]
        stems = {s for t in top for s in t.split()}
        desc = re.split(r"\bP-\s", self.catalog.courses[cid].description)[0]
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", desc) if s.strip()]
        best = max(sentences, key=lambda s: len(stems & set(analyze(s))), default="")
        if len(best) > 160:
            best = best[:157].rsplit(" ", 1)[0] + "..."
        return matched, best

    def recommend(self, query: str, program_id: str | list[str], exclude: set[str] = frozenset(), top_n: int = 5,
                  goal_label: str | None = None, skip=None) -> list[Recommendation]:
        """Top courses for `query` that the student could still take toward their program(s).

        `program_id` is one id or a list (major(s) and minors; the first is the main one).
        `skip(course)` can return a reason to leave a course out (e.g. School of Business rules)."""
        ids = [program_id] if isinstance(program_id, str) else list(program_id)
        needs = [n for pid in ids for n in program_needs(self.catalog.programs[pid])]
        excluded = self.catalog.excluded_from(ids[0])
        required = {o for n in needs if n.item.kind in ("course", "choose") for o in n.item.options}
        hours_needs = [n for n in needs if n.item.kind == "hours"]
        several = len(ids) > 1
        q = self.vectorizer.transform([query])
        ranked = sorted(self.scores(query).items(), key=lambda kv: (-kv[1], kv[0]))
        out: list[Recommendation] = []
        for cid, score in ranked:
            course = self.catalog.courses[cid]
            if (score <= 0 or cid in exclude or cid in required or schedulable_reason(course)
                    or (cid in excluded and excluded[cid] is None) or course.credits <= 0
                    or (skip and skip(course))):
                continue
            if any(r.course_id in course.exclusive_with or r.title == course.title for r in out):
                continue          # same course under another subject (credit not allowed for both)
            if course.number.endswith("L") and f"{course.subject} {course.number[:-1]}" in self.catalog.courses:
                continue          # a lab: recommend the lecture (the lab comes with it as a corequisite)
            matched, quote = self._explain(cid, q)
            toward = [(f"{self.catalog.programs[n.program_id].name}: " if several else "")
                      + n.label.split(": ", 1)[-1] for n in hours_needs
                      if fits_hours_rule(course, n, n.program_id, self.catalog)]
            goal = f" ({goal_label})" if goal_label else ""
            reason = f"Matches your goal{goal} through {', '.join(matched)}: “{quote}”"
            out.append(Recommendation(cid, course.title, score, matched, quote, toward, reason))
            if len(out) == top_n:
                break
        return out


def career_query(career_id: str | None, text: str = "", careers: dict[str, Career] | None = None) -> tuple[str, str]:
    """(query text, readable goal label) from a pick-list id and/or free text."""
    careers = careers or load_careers()
    parts, label = [], []
    if career_id:
        if career_id not in careers:
            raise ValueError(f"unknown career {career_id!r}; choose from: {', '.join(careers)}")
        parts.append(careers[career_id].keywords)
        label.append(careers[career_id].label)
    if text:
        parts.append(text)
        label.append(f"'{text}'" if not career_id else "your words")
    return " ".join(parts), " + ".join(label)


def recommend_for_student(catalog: Catalog, student: StudentProfile, top_n: int = 5,
                          recommender: Recommender | None = None) -> tuple[list[Recommendation], dict[str, int]]:
    """Top-N recommendations and all relevance scores (for the planner) from the profile's career fields."""
    query, label = career_query(student.career, student.career_text)
    if not query:
        return [], {}
    rec = recommender or Recommender(catalog)
    done = student.completed_ids
    return rec.recommend(query, student.programs, done, top_n, label, skip=business_skip(catalog, student)), \
        rec.scores(query)


def business_skip(catalog: Catalog, student: StudentProfile):
    """For non-business students: leave out School of Business courses they'd need permission for.
    (Business majors can take them after admission, so they stay in.)"""
    settings = load_settings(catalog)
    if is_business_student(catalog, student, settings):
        return None
    last = student.plan_terms()[-1] if student.plan_terms() else student.graduation_term
    admission = admission_courses(catalog)
    return lambda course: business_reason(course, last, student, catalog, settings, admission)
