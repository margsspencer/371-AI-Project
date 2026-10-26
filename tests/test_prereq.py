"""Tests for the prerequisite logic parser and checker.

Covers the required edge cases: OR-prerequisites, implied subjects, mixed and/or
(team rule), parentheses, AP credit, and a failed course.
"""

from gradplanner.parser.prereq import (
    CourseAtom, LevelAtom, ManualAtom, cnf_satisfied, cnf_to_text, parse_prereq, unmet_clauses,
)


def codes(cnf):
    """Turn CNF into lists of plain strings for easy comparison."""
    return [[str(a) for a in clause] for clause in cnf]


def test_empty_and_none():
    assert parse_prereq(None).status == "none"
    assert parse_prereq("  ").cnf == []


def test_single_course():
    r = parse_prereq("CSC 111")
    assert r.status == "ok" and codes(r.cnf) == [["CSC 111"]]


def test_and_with_implied_subject():
    # "CSC 231 and 250": 250 inherits the CSC subject
    assert codes(parse_prereq("CSC 231 and 250").cnf) == [["CSC 231"], ["CSC 250"]]


def test_simple_or():
    assert codes(parse_prereq("STA 310 or MTH 357").cnf) == [["STA 310", "MTH 357"]]


def test_mixed_and_or_uses_team_rule():
    r = parse_prereq("CSC 201 and MTH 121 or 205")
    assert codes(r.cnf) == [["CSC 201"], ["MTH 121", "MTH 205"]]
    assert r.status == "resolved_by_rule"


def test_commas_and_semicolons_separate_and_clauses():
    r = parse_prereq("CSC 201 and 270, MTH 112, and MTH 121 or 205")
    assert codes(r.cnf) == [["CSC 201"], ["CSC 270"], ["MTH 112"], ["MTH 121", "MTH 205"]]
    assert codes(parse_prereq("MTH 112; and MTH 121 or 205").cnf) == [["MTH 112"], ["MTH 121", "MTH 205"]]


def test_parentheses_are_respected_and_distributed():
    # (A and B) or C  ==  (A or C) and (B or C)
    r = parse_prereq("(MTH 121 and 251) or MTH 205")
    assert r.status == "ok"
    assert codes(r.cnf) == [["MTH 121", "MTH 205"], ["MTH 251", "MTH 205"]]


def test_one_of_the_following_list():
    r = parse_prereq("STA 112, and one of the following courses: STA 214, STA 261, STA 365")
    assert codes(r.cnf) == [["STA 112"], ["STA 214", "STA 261", "STA 365"]]


def test_either_filler_word_ignored():
    assert codes(parse_prereq("STA 112, and either STA 310 or MTH 357").cnf) == [["STA 112"], ["STA 310", "MTH 357"]]


def test_level_atom():
    r = parse_prereq("any 200-level CSC course")
    assert r.cnf == [[LevelAtom("CSC", 200)]]
    assert cnf_satisfied(r.cnf, {"CSC 250"})
    assert not cnf_satisfied(r.cnf, {"CSC 112", "MTH 250"})


def test_unknown_words_become_manual_not_dropped():
    r = parse_prereq("senior STA major")
    assert r.status == "manual" and r.cnf == [[ManualAtom("senior STA major")]]
    assert not cnf_satisfied(r.cnf, {"STA 999"})   # manual conditions are never auto-satisfied


def test_bracket_manual_condition_in_overrides():
    r = parse_prereq("STA 111 or [AP Statistics score 4 or 5]")
    assert r.cnf == [[CourseAtom("STA 111"), ManualAtom("AP Statistics score 4 or 5")]]


def test_unparseable_text_fails_safely():
    r = parse_prereq("CSC 201 and and")
    assert r.status == "failed"
    assert isinstance(r.cnf[0][0], ManualAtom)


def test_or_prerequisite_satisfied_by_either_option():
    cnf = parse_prereq("CSC 201 and MTH 121 or 205").cnf
    assert cnf_satisfied(cnf, {"CSC 201", "MTH 121"})
    assert cnf_satisfied(cnf, {"CSC 201", "MTH 205"})
    assert not cnf_satisfied(cnf, {"CSC 201"})


def test_ap_credit_counts_as_completed_course():
    # AP CS score 4/5 -> credit for CSC 111 (CS department page). The profile records AP credit as
    # the equivalent course, so it satisfies CSC 112's prerequisite like a completed course.
    cnf = parse_prereq("CSC 111").cnf
    completed_incl_ap = {"CSC 111"}
    assert cnf_satisfied(cnf, completed_incl_ap)


def test_failed_course_does_not_satisfy_and_is_reported():
    cnf = parse_prereq("CSC 112 and MTH 117").cnf
    passed = {"CSC 112"}          # MTH 117 was failed, so it is not in the passed set
    assert not cnf_satisfied(cnf, passed)
    assert codes(unmet_clauses(cnf, passed)) == [["MTH 117"]]


def test_cnf_to_text():
    assert cnf_to_text(parse_prereq("CSC 201 and MTH 121 or 205").cnf) == "CSC 201 AND (MTH 121 OR MTH 205)"
