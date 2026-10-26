"""Tests for turning Bulletin course HTML into RawCourse records."""

from pathlib import Path

import pytest

from gradplanner.parser.courses import parse_courses_page, split_sentences

FIXTURE = Path(__file__).parent / "fixtures" / "courses_sample.html"


@pytest.fixture(scope="module")
def courses():
    return {c.id: c for c in parse_courses_page(FIXTURE.read_text(), "fixture")}


def test_all_blocks_found(courses):
    assert set(courses) == {"TST 101", "TST 201", "TST 301", "TST 105L", "TST 391", "TST 385", "TST 999"}


def test_simple_course_has_credits_tag_and_no_prereq(courses):
    c = courses["TST 101"]
    assert c.title == "Intro to Testing"
    assert (c.credits_min, c.credits_max) == (4, 4)
    assert c.tags == ["D"]
    assert c.prereq_raw is None


def test_prereq_with_optional_override_and_two_tags(courses):
    c = courses["TST 201"]
    assert c.prereq_raw == "TST 101 and MTH 117"
    assert c.override_allowed and not c.override_required
    assert c.tags == ["D", "QDA"]
    assert c.linked_codes == ["TST 101", "MTH 117"]


def test_title_with_period_and_offering_sentence(courses):
    c = courses["TST 301"]
    assert c.title == "Mixed Logic, Part 1"
    assert c.prereq_raw == "TST 201 and 270, MTH 121 or 205"
    assert c.offering_raw == ["Offered fall semester."]


def test_corequisite_and_lab_code(courses):
    c = courses["TST 105L"]
    assert c.coreq_raw == "TST 101"
    assert c.prereq_raw is None
    assert c.offering_raw == []          # "offered by the department" is not timing info
    assert len(c.exclusion_notes) == 1


def test_variable_credit_and_required_override(courses):
    c = courses["TST 391"]
    assert (c.credits_min, c.credits_max) == (1, 3)
    assert c.prereq_raw == "any 200-level TST course"
    assert c.override_required and not c.override_allowed


def test_parenthetical_prereq_kept_whole(courses):
    c = courses["TST 385"]
    assert c.prereq_raw == "TST 201 (and BIO 150, 150L if taken as BIO 385)"
    assert c.credit_not_both == ["Credit not allowed for both TST 385 and BIO 385."]


def test_missing_credits_is_reported_not_crashed(courses):
    c = courses["TST 999"]
    assert c.credits_min is None
    assert any(p.startswith("credits") for p in c.problems)


def test_split_sentences_ignores_periods_inside_parentheses():
    assert split_sentences("A (b. c) d. E f.") == ["A (b. c) d.", "E f."]
