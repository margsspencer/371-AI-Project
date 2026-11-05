"""All-departments expansion: prerequisite wording cleanup, new table shapes, discovery, ids."""

import pytest

from gradplanner.parser.prereq import clean_prereq_text, cnf_to_text, parse_prereq
from gradplanner.parser.programs import parse_program_page
from gradplanner.scraper.discover import _links, subject_names, Page


def parsed(text, subject="TST", known=None):
    c = clean_prereq_text(text, subject, known)
    return cnf_to_text(parse_prereq(c.text).cnf), c


# ---------------------------------------------------------------- prerequisite wording

@pytest.mark.parametrize("raw, expected", [
    ("BIO 370 / BMB 370 / CHM 370 or BIO 332", "(BIO 370 OR BMB 370 OR CHM 370 OR BIO 332)"),
    ("BIO/BMB/ CHM 370", "(BIO 370 OR BMB 370 OR CHM 370)"),
    ("CHM 111 /111L", "CHM 111 AND CHM 111L"),
    ("ACC 211 and 212 with a grade of C or better", "ACC 211 AND ACC 212"),
    ("minimum of C in ACC 111", "ACC 111"),
    ("GER 110, GER 112, or GER 113", "(GER 110 OR GER 112 OR GER 113)"),
    ("MTH 205 (or MTH 121 and MTH 251)", "(MTH 205 OR MTH 121) AND (MTH 205 OR MTH 251)"),
    ("STA111 and NEU 200", "STA 111 AND NEU 200"),
    ("One PHI course (221 strongly recommended)", "any PHI 100+"),
    ("a 200-level CRW course", "any CRW 200+"),
    ("ACC 390 (Not open to students who have taken ACC 392)", "ACC 390"),
])
def test_cleanup_rules(raw, expected):
    assert parsed(raw)[0] == expected


def test_level_phrase_uses_own_subject():
    assert parsed("200-level course", "GER")[0] == "any GER 200+"


def test_or_equivalent_is_a_manual_option():
    text, _ = parsed("CHI 101 or equivalent", "CHI")
    assert text == "(CHI 101 OR [manual: equivalent])"


def test_class_standing_and_corequisites_are_separated():
    text, c = parsed("BEM 201 and FIN 231 with C or better; C- FIN 203")
    assert text == "BEM 201 AND FIN 231" and c.coreq == "FIN 203"
    _, c = parsed("Junior or senior standing")
    assert c.standing == "junior" and c.text == ""
    _, c = parsed("EGR 311, EGR 313, P or C- EGR 312")
    assert c.coreq == "EGR 312"


def test_corequisite_for_some_majors_only_is_dropped():
    text, c = parsed("or C- (For BEM majors only) BEM 287", "BEM")
    assert c.coreq == "" and any("some majors" in n for n in c.notes)


def test_course_range_uses_catalog():
    known = {"SPA 310", "SPA 312", "SPA 319", "SPA 320"}
    text, _ = parsed("any course in SPA 310 - SPA 319 sequence", "SPA", known)
    assert text == "(SPA 310 OR SPA 312 OR SPA 319)"


def test_any_two_of_the_following():
    text, _ = parsed("any two of the following with associated labs: CHM 122 (or CHM 123), CHM 280 or BIO 265", "BMB")
    assert text == "(CHM 122 OR CHM 123 OR CHM 280) AND (CHM 122 OR CHM 123 OR BIO 265) AND (CHM 280 OR BIO 265)"


def test_bare_lab_number_gets_own_subject():
    assert parsed("a grade of C or higher in 150L", "BIO")[0] == "BIO 150L"


def test_subject_names_are_uppercase_codes_only():
    text, _ = parsed("One course in art history", "ART")
    assert "ART" not in text.replace("[manual", "")      # stays a manual condition


# ---------------------------------------------------------------- program pages

def link(code):
    return f'<a class="bubblelink code" href="/search/?P={code}">{code}</a>'


def row(cells, cls="", indent=False):
    return f'<tr class="{cls}">{cells}</tr>'


def course_row(code, extra=""):
    return f'<tr><td class="codecol">{link(code)}{extra}</td><td>Title</td><td class="hourscol">3</td></tr>'


def comment_row(text, cls="", indent=False):
    span = f'<span class="courselistcomment">{text}</span>'
    if indent:
        span = f'<div class="blockindent">{span}</div>'
    return f'<tr class="{cls}"><td colspan="2">{span}</td><td class="hourscol"></td></tr>'


def page(title, body):
    return (f'<html><body><h1 class="page-title">{title}</h1>'
            f'<div id="requirementstextcontainer">{body}</div></body></html>')


def table(rows):
    return '<table class="sc_courselist"><tr class="hidden"><td>Code</td></tr>' + "".join(rows) + "</table>"


def test_lab_bundle_cross_listing_and_total_row():
    html = page("Testing, B.S.", table([
        course_row("BIO 150", ' &amp; ' + link("150L")),
        '<tr><td class="codecol"><a class="bubblelink code">ENG/AES 357</a></td><td>T</td><td>3</td></tr>',
        '<tr class="listsum"><td>Total Hours</td><td>18</td></tr>',
    ]))
    prog = parse_program_page(html, "TST-BS", "fixture")
    assert [it.options for it in prog.groups[0].items] == [["BIO 150"], ["BIO 150L"], ["ENG 357"]]
    assert prog.problems == []


def test_choice_list_starting_with_text_row_and_linked_list():
    html = page("Testing, B.S.", table([
        comment_row("Choose two of the following"),
        comment_row("Any TST course at the 300-level"),
        course_row("TST 301"), course_row("TST 302"),
        comment_row('<a href="#extra">Choose one of the following</a>'),
    ]) + '<h3 id="extra">Extra</h3>' + table([course_row("MTH 111"), course_row("MTH 112")]))
    items = parse_program_page(html, "TST-BS", "fixture").groups[0].items
    assert items[0].kind == "choose" and items[0].n == 2 and items[0].options == ["TST 301", "TST 302"]
    assert items[0].free_text_options == ["Any TST course at the 300-level"]
    assert items[1].kind == "choose" and items[1].options == ["MTH 111", "MTH 112"]
    assert len(items) == 2                     # the linked table isn't read as required courses


def test_group_tables_each_of_the_following_and_schedule_skipped():
    html = page("Philosophy, B.A.",
                "<p>Requires 27 hours.</p>" + table([
                    comment_row("One course from each of the following groups"),
                    comment_row("I. Ancient", indent=True), comment_row("II. Modern", indent=True),
                ]) + "<h3>I. Ancient</h3>" + table([course_row("PHI 232"), course_row("PHI 331")])
                + "<h3>II. Modern</h3>" + table([course_row("PHI 241")])
                + "<p>A typical schedule is:</p>" + table([course_row("PHI 999")]))
    prog = parse_program_page(html, "PHI-BA", "fixture")
    items = [it for g in prog.groups for it in g.items]
    assert [(it.kind, it.options) for it in items] == [("choose", ["PHI 232", "PHI 331"]), ("choose", ["PHI 241"])]
    assert prog.support == "full"


def test_notes_and_generic_rules():
    names = {"greek": "GRK", "computer science": "CSC"}
    html = page("Greek, B.A.", table([
        comment_row("ACC 111 and BEM 201 count toward Core Requirements"),
        comment_row("Select 24 hours in Greek"),
        comment_row("Additional hours beyond GRK 219, no more than 9 hours at the 100 level"),
    ]))
    prog = parse_program_page(html, "GRK-BA", "fixture", subject_names=names)
    note, hours, hard = [it for g in prog.groups for it in g.items]
    assert note.kind == "note"
    assert (hours.kind, hours.hours, hours.subject) == ("hours", 24, "GRK")
    assert hard.kind == "free_text"            # a real requirement with a limit: left for an advisor
    assert prog.support == "partial"


# ---------------------------------------------------------------- discovery and ids

def test_discovery_links_and_subject_names():
    html = ('<a href="/courses-az/csc/">Computer Science (CSC)</a><a href="/courses-az/csc/x.pdf">pdf</a>'
            '<a href="/courses-az/">index</a><a href="/courses-az/mth/#top">Mathematics (MTH)</a>')
    assert _links(html, "/courses-az/") == [("/courses-az/csc/", "Computer Science (CSC)"),
                                             ("/courses-az/mth/", "Mathematics (MTH)")]
    pages = [Page("courses_csc", "/courses-az/csc/", "courses", "CSC", "Computer Science (CSC)")]
    assert subject_names(pages) == {"CSC": "Computer Science"}


def test_real_catalog_ids_and_coverage():
    from gradplanner.db.store import default_db_path, load_catalog
    if not default_db_path().exists():
        pytest.skip("run scripts/build_db.py first")
    cat = load_catalog()
    if len(cat.programs) < 50:
        pytest.skip("run scripts/scrape.py --all and scripts/build_db.py for all departments")
    assert cat.programs["CSC-BS"].name == "Computer Science, B.S."
    assert cat.programs["MTH-BS"].name == "Mathematics, B.S."            # plain name keeps the plain id
    assert cat.programs["MTH-BS-APPLIED"].name == "Applied Mathematics, B.S."
    assert {"ACC-BS", "FIN-BS", "BEM-BS"} <= set(cat.programs)
    assert not any(p.name.startswith("Certificate") for p in cat.programs.values())
    assert sum(p.support == "full" for p in cat.programs.values()) >= 50


def test_or_similar_course_including():
    text, _ = parsed("ECN 150 and STA 111, (or similar course, including ANT 380; BIO 380; BEM 201 or STA 311)", "ECN")
    assert text == "ECN 150 AND (STA 111 OR ANT 380 OR BIO 380 OR BEM 201 OR STA 311)"
