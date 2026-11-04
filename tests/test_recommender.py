"""Career recommender: ranking, explanations, eligibility, and its effect on the planner."""

import pytest

from gradplanner.planner import plan_for_student, validate_plan
from gradplanner.planner.student import CompletedCourse
from gradplanner.recommender import Recommender, career_query, load_careers
from gradplanner.recommender.tfidf import analyze, course_text, normalize
from test_planner import SETTINGS, catalog, student

DESCRIPTIONS = {
    "TST 302": ("Machine Learning", "Supervised and unsupervised learning with neural networks and regression."),
    "TST 303": ("Computer Security", "Cryptography, authentication, and network security. Students analyze attacks."),
    "TST 301": ("Algorithms", "Design and analysis of algorithms. P- TST 201."),
    "TST 395": ("Deep Learning Research", "Neural networks and deep learning research with a faculty member."),
    "TST 201": ("Data Structures", "Lists, trees, graphs, and hashing."),
}


def described_catalog():
    cat = catalog()
    for cid, (title, desc) in DESCRIPTIONS.items():
        cat.courses[cid].title, cat.courses[cid].description = title, desc
    cat.courses["TST 302"].exclusive_with = []
    cat.courses["TST 303"].exclusive_with = []
    return cat


def test_word_handling():
    assert normalize("networks") == "network" and normalize("statistical") == normalize("statistics")
    terms = analyze("Neural networks and deep learning, regression")
    assert "neural network" in terms and "deep learn" in terms
    assert "network deep" not in terms          # no phrase across a dropped word
    assert "learn regression" not in terms      # or across a comma


def test_ranking_and_reason():
    rec = Recommender(described_catalog())
    top = rec.recommend("machine learning, neural networks", "TST-BS", goal_label="ML")
    assert top[0].course_id == "TST 302"
    assert "neural networks" in top[0].matched
    assert top[0].quote.startswith("Supervised")
    assert "(ML)" in top[0].reason
    assert 0 < top[0].score <= 100
    assert rec.recommend("cryptography security", "TST-BS")[0].course_id == "TST 303"


def test_only_eligible_courses():
    rec = Recommender(described_catalog())
    ids = [r.course_id for r in rec.recommend("neural networks deep learning algorithms", "TST-BS",
                                              exclude={"TST 302"}, top_n=10)]
    assert "TST 395" not in ids       # needs department permission
    assert "TST 302" not in ids       # already done
    assert "TST 301" not in ids       # required anyway, not a choice


def test_counts_toward_and_prerequisite_text_ignored():
    rec = Recommender(described_catalog())
    top = rec.recommend("machine learning", "TST-BS")[0]
    assert top.counts_toward == ["3 h of TST 300+"]
    text = course_text(rec.catalog.courses["TST 301"])
    assert "Design and analysis" in text and "TST 201" not in text     # prerequisite sentence dropped


def test_career_pick_list():
    careers = load_careers()
    assert len(careers) >= 6
    query, label = career_query("software-engineer", "web apps", careers)
    assert "software" in query and "web apps" in query and label.startswith("Software engineer")
    with pytest.raises(ValueError):
        career_query("astronaut", careers=careers)


def test_planner_follows_relevance():
    cat = described_catalog()
    rec = Recommender(cat)
    for query, expected in (("machine learning neural networks", "TST 302"), ("cryptography security", "TST 303")):
        st = student(completed=[CompletedCourse("TST 101")])
        plan = plan_for_student(cat, st, SETTINGS, time_limit=10, workers=4, relevance=rec.scores(query))
        assert plan.ok and validate_plan(plan, cat, st, SETTINGS) == []
        assert plan.term_of(expected) is not None


# ---------------------------------------------------------------- real data: 3 sample careers

@pytest.mark.parametrize("career, must_include", [
    ("machine-learning-engineer", {"CSC 374", "CSC 375"}),
    ("cybersecurity-analyst", {"CSC 348"}),
    ("software-engineer", {"CSC 331"}),
    ("data-scientist", {"STA 112"}),
])
def test_real_top5_is_sensible(career, must_include):
    from gradplanner.db.store import default_db_path, load_catalog

    if not default_db_path().exists():
        pytest.skip("run scripts/build_db.py first")
    query, label = career_query(career)
    top = Recommender(load_catalog()).recommend(query, "CSC-BS", goal_label=label)
    ids = {r.course_id for r in top}
    assert must_include <= ids, [(r.course_id, r.score) for r in top]
    assert all(r.reason and r.matched for r in top)
