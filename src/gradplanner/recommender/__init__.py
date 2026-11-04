"""Career-based course recommender (milestone 5): TF-IDF over Bulletin course descriptions."""

from gradplanner.recommender.tfidf import (
    Recommendation, Recommender, career_query, load_careers, recommend_for_student,
)

__all__ = ["Recommendation", "Recommender", "career_query", "load_careers", "recommend_for_student"]
