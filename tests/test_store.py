"""Database round trip and Course helpers (offerings, alternate years)."""

from gradplanner.db.store import load_catalog, save_catalog
from gradplanner.models import Catalog, Course, DegreeRule, Offering
from gradplanner.parser.core import CoreCategory
from gradplanner.parser.prereq import parse_prereq
from gradplanner.parser.programs import Program, RequirementGroup, RequirementItem


def make_course(cid, prereq=None, offerings=None):
    subject, number = cid.split()
    return Course(cid, subject, number, cid + " title", 3, 3, "desc", ["D"], parse_prereq(prereq).cnf,
                  prereq, "ok", [], None, False, False,
                  offerings or [Offering("fall"), Offering("spring")])


def tiny_catalog():
    courses = {
        "TST 101": make_course("TST 101"),
        "TST 201": make_course("TST 201", "TST 101 and MTH 121 or 205"),
        "TST 301": make_course("TST 301", "any 200-level TST course",
                               [Offering("spring", "odd", "override")]),
    }
    prog = Program("TST-BS", "Testing, B.S.", "major", "BS", "TST", "intro", "fixture",
                   [RequirementGroup("Core", [RequirementItem("course", "TST 101", ["TST 101"]),
                                              RequirementItem("hours", "300+", hours=3, subject="TST",
                                                              min_level=300)])])
    core = [CoreCategory("DIV-I", "divisional", "I", 2, tag="D", placeholder=True)]
    rules = {"total_hours": DegreeRule("total_hours", 120, "fixture", "for a total of 120 credit hours")}
    return Catalog("test", courses, {"TST-BS": prog}, core, rules, [])


def test_round_trip(tmp_path):
    original = tiny_catalog()
    loaded = load_catalog(save_catalog(original, tmp_path / "t.db"))
    assert loaded.courses.keys() == original.courses.keys()
    assert loaded.courses["TST 201"].prereq == original.courses["TST 201"].prereq
    assert loaded.courses["TST 301"].prereq == original.courses["TST 301"].prereq   # LevelAtom survives
    assert loaded.programs["TST-BS"].groups[0].items[1].min_level == 300
    assert loaded.rule("total_hours") == 120
    assert loaded.core[0].courses_required == 2


def test_alternate_year_offering():
    c = tiny_catalog().courses["TST 301"]     # spring, odd years only
    assert c.is_offered("spring", 2027)
    assert not c.is_offered("spring", 2028)
    assert not c.is_offered("fall", 2027)


def test_level_property_handles_lab_suffix():
    assert make_course("MTH 105L").level == 105
