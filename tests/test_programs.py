"""Tests for program requirement-table parsing and exclusion mapping."""

from pathlib import Path

from gradplanner.parser.programs import Program, exclusions_for_course, parse_program_page

HTML = (Path(__file__).parent / "fixtures" / "program_sample.html").read_text()


def test_program_metadata():
    p = parse_program_page(HTML, "TST-BS", "fixture")
    assert (p.name, p.type, p.degree, p.subject) == ("Testing, B.S.", "major", "BS", "TST")
    assert p.intro.startswith("Requires")          # drop-cap span joined back together
    assert p.problems == []


def test_groups_and_items():
    p = parse_program_page(HTML, "TST-BS", "fixture")
    assert [g.label for g in p.groups] == ["Required Major Courses", "Co-Requirements"]
    major = p.groups[0].items
    assert major[0].kind == "course" and major[0].options == ["TST 101"]
    assert major[1].options == ["MTH 121", "MTH 205"]      # "or" row joins the item above


def test_hours_rules():
    major = parse_program_page(HTML, "TST-BS", "fixture").groups[0].items
    explicit, implied = major[2], major[3]
    assert (explicit.kind, explicit.hours, explicit.subject, explicit.min_level) == ("hours", 12, "CSC", 300)
    assert explicit.needs_review is None
    assert (implied.hours, implied.subject, implied.min_level) == (3, "TST", 191)
    assert "NEEDS HUMAN REVIEW" in implied.needs_review


def test_choose_one_with_free_text_option():
    item = parse_program_page(HTML, "TST-BS", "fixture").groups[1].items[0]
    assert item.kind == "choose" and item.n == 1
    assert item.options == ["MTH 112", "STA 111"]
    assert item.free_text_options == ["Any course approved by the department"]
    assert item.needs_review


def test_exclusion_mapping():
    progs = [Program("CSC-BS", "", "major", "BS", "CSC", "", ""),
             Program("CSC-BA", "", "major", "BA", "CSC", "", "")]
    ex, unmatched = exclusions_for_course(
        "CSC 191", "CSC", ["Not to be counted toward the bachelor of science in computer science."], progs)
    assert [(e.program_id, e.max_hours) for e in ex] == [("CSC-BS", None)]
    ex, _ = exclusions_for_course(
        "CSC 393", "CSC", ["No more than three hours may be counted toward a computer science major or minor."], progs)
    assert {(e.program_id, e.max_hours) for e in ex} == {("CSC-BS", 3), ("CSC-BA", 3)}
    _, unmatched = exclusions_for_course("CSC 1", "CSC", ["Does not count for anything we know."], progs)
    assert unmatched
