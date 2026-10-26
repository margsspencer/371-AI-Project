"""Tests for Core Curriculum and degree-rule parsing."""

from pathlib import Path

from gradplanner.parser.core import parse_advising_page, parse_core_page
from gradplanner.parser.prereq import cnf_satisfied

HTML = (Path(__file__).parent / "fixtures" / "core_sample.html").read_text()


def test_degree_rules_with_source_text():
    _, rules, problems = parse_core_page(HTML, "fixture")
    by_key = {r.key: r for r in rules}
    assert by_key["total_hours"].value == 120
    assert "120 credit hours" in by_key["total_hours"].source_text
    assert by_key["min_hours_for_core_course"].value == 3
    assert problems == []


def test_basic_requirements():
    cats = {c.id: c for c in parse_core_page(HTML, "fixture")[0]}
    assert cats["BASIC-FYS"].timing == "year1"
    wri = cats["BASIC-WRI"].course_cnf
    assert cnf_satisfied(wri, {"WRI 111"})
    assert cnf_satisfied(wri, {"WRI 109", "WRI 110"})
    assert not cnf_satisfied(wri, {"WRI 109"})
    assert cats["BASIC-LANG"].placeholder
    assert cats["BASIC-HES"].timing == "by_end_of_year2"


def test_divisional_and_21cs():
    cats = {c.id: c for c in parse_core_page(HTML, "fixture")[0]}
    assert cats["DIV-I"].courses_required == 2 and cats["DIV-I"].tag == "D"
    assert cats["DIV-V"].courses_required == 2
    assert "two divisions" in cats["DIV-I"].rules_note
    assert cats["21CS-EI"].tag == "EI"
    assert not any(c.startswith("21CS-") and c != "21CS-EI" for c in cats)


def test_missing_rule_is_reported_not_guessed():
    rules, problems = parse_advising_page("<div id='textcontainer'><p>Nothing here.</p></div>", "fixture")
    assert rules == []
    assert any("max_hours_per_semester" in p for p in problems)
