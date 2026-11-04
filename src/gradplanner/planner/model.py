"""The planner: places courses into semesters with Google OR-Tools CP-SAT.

CP-SAT is a constraint solver. We describe the problem with yes/no variables
and rules, and the solver finds an assignment that follows every rule while
scoring as well as possible on our objective. We never write the search
ourselves, so a plan it returns follows the rules by construction (and
validate_plan.py checks again, independently).

Variables
  take[c, t]   1 if course c is placed in term t (only created when c is offered in t)
  slot[k, t]   number of generic core slots of category k in term t ("Division I: choose a course")
  elec[t]      number of 3-hour free-elective slots in term t
  hrs[c, n]    1 if course c's hours count toward elective-hours rule n ("12 h of CSC 300+")
  cov[c, k]    1 if course c counts toward core category k

Rules (each group has an on/off switch, used to explain "no valid plan")
  course rules   each course at most once; prerequisites in an earlier term; corequisites
                 in the same or earlier term; class standing; "credit not allowed for both"
  term limits    hours per term <= the student's maximum (never above the Bulletin's 17)
  major          every required course; every "choose" list; every elective-hours rule
  core           every core category is covered by a course or a slot; timing (FYS/WRI in
                 year 1, HES by the end of year 2); one language course per term
  total hours    at least 120 hours by graduation

Objective, most important first
  1. no more hours than needed       2. loads close to the normal 15 h
  3. cover core with courses already in the plan instead of extra slots
  4. no unneeded catalog courses; prefer the student's interests
  5. required courses and core slots as early as possible

Assumption: a course may count toward the major, one division, one 21CS
category, and QR at the same time.
"""

from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model

from gradplanner.models import Catalog, Course, CourseAtom, LevelAtom
from gradplanner.planner.plan import Plan, PlanItem, TermPlan
from gradplanner.planner.precheck import precheck
from gradplanner.planner.requirements import (
    PlannerSettings, core_needs, countable_hours, course_counts_for_core,
    fits_hours_rule, load_settings, program_needs, rule_or, schedulable_reason,
)
from gradplanner.planner.student import StudentProfile
from gradplanner.planner.terms import Term

SCALE = 10                       # hours are stored in tenths so the solver works with whole numbers
ELECTIVE_HOURS = 3
STANDING_RULES = {"sophomore": "standing_sophomore_hours", "junior": "standing_junior_hours",
                  "senior": "standing_senior_hours"}

SWITCHES = {
    "course_rules": "prerequisites, corequisites and class standing",
    "term_limits": "the maximum hours per semester",
    "major": "the major/minor requirements",
    "core": "the Core Curriculum requirements and their timing",
    "total": "the 120-hour graduation total",
}


def h(hours: float) -> int:
    return int(round(hours * SCALE))


@dataclass
class _Ctx:
    catalog: Catalog
    student: StudentProfile
    settings: PlannerSettings
    terms: list[Term]


def plan_for_student(catalog: Catalog, student: StudentProfile, settings: PlannerSettings | None = None,
                     time_limit: float = 10.0, workers: int = 8) -> Plan:
    """Build the best plan for `student`, or explain why none exists."""
    settings = settings or load_settings()
    if student.program not in catalog.programs:
        raise ValueError(f"unknown program {student.program!r}; known: {sorted(catalog.programs)}")
    ctx = _Ctx(catalog, student, settings, student.plan_terms())
    plan = Plan(student.name, student.program, "unknown")

    blocking, notes = precheck(catalog, student, settings, ctx.terms)
    plan.notes += notes
    if blocking:
        plan.status = "infeasible"
        plan.explanations = blocking
        return plan

    builder = _Builder(ctx, plan)
    builder.build()
    builder.solve(time_limit, workers)
    return plan


class _Builder:
    def __init__(self, ctx: _Ctx, plan: Plan) -> None:
        self.ctx, self.plan = ctx, plan
        self.cat, self.st, self.terms = ctx.catalog, ctx.student, ctx.terms
        self.program = self.cat.programs[self.st.program]
        self.m = cp_model.CpModel()
        self.sw = {name: self.m.new_bool_var(f"switch_{name}") for name in SWITCHES}

        # Completed courses we know about, and hours already earned.
        self.done: dict[str, str] = {}
        for c in self.st.completed:
            if c.course in self.cat.courses:
                self.done[c.course] = c.source
            else:
                plan.notes.append(f"{c.course} is not in our catalog; list it under outside_courses instead.")
        self.done_hours = (sum(self.cat.courses[c].credits for c in self.done)
                           + sum(o.hours for o in self.st.outside_courses))

        self.candidates: dict[str, Course] = {}
        for cid, course in self.cat.courses.items():
            if cid not in self.done and schedulable_reason(course) is None:
                self.candidates[cid] = course

        self.take: dict[str, dict[int, cp_model.IntVar]] = {}
        self.taken: dict[str, cp_model.LinearExpr] = {}
        self.slot: dict[str, dict[int, cp_model.IntVar]] = {}
        self.elec: dict[int, cp_model.IntVar] = {}
        self.load: dict[int, cp_model.IntVar] = {}
        self.hrs: dict[tuple[str, str], cp_model.IntVar] = {}
        self.cov: dict[tuple[str, str], cp_model.IntVar] = {}
        self.pneeds = program_needs(self.program)
        self.cneeds = [n for n in core_needs(self.cat, self.st, ctx.settings) if n.count > 0]
        self.objective_terms: list = []

    # ------------------------------------------------------------------ build

    def build(self) -> None:
        self._course_vars()
        self._loads()
        self._course_rules()
        self._major()
        self._core()
        self._total()
        self._objective()

    def _course_vars(self) -> None:
        for cid, course in self.candidates.items():
            per_term = {ti: self.m.new_bool_var(f"take[{cid},{t}]")
                        for ti, t in enumerate(self.terms) if course.is_offered(t.season, t.year)}
            if not per_term:
                continue
            self.take[cid] = per_term
            self.taken[cid] = sum(per_term.values())
            self.m.add(self.taken[cid] <= 1)

    def _loads(self) -> None:
        for ti, term in enumerate(self.terms):
            self.elec[ti] = self.m.new_int_var(0, 6, f"elec[{term}]")
        for need in self.cneeds:
            self.slot[need.id] = {}
            last_year = need.last_year
            if last_year is not None and last_year < self.st.year_index(self.terms[0]):
                # The deadline has already passed: allow it as soon as possible instead.
                last_year = self.st.year_index(self.terms[0])
                self.plan.notes.append(f"{need.category.name} should already be done "
                                       f"({need.category.timing}); planned as early as possible. See your advisor.")
            for ti, term in enumerate(self.terms):
                if last_year is not None and self.st.year_index(term) > last_year:
                    continue
                self.slot[need.id][ti] = self.m.new_int_var(0, 1 if need.one_per_term else need.count,
                                                            f"slot[{need.id},{term}]")
        bulletin_max = rule_or(self.cat, "max_hours_per_semester", 17)
        for ti, term in enumerate(self.terms):
            expr = h(ELECTIVE_HOURS) * self.elec[ti]
            expr += sum(h(self.candidates[c].credits) * v[ti] for c, v in self.take.items() if ti in v)
            expr += sum(h(n.slot_hours) * self.slot[n.id][ti] for n in self.cneeds if ti in self.slot[n.id])
            self.load[ti] = self.m.new_int_var(0, h(40), f"load[{term}]")
            self.m.add(self.load[ti] == expr)
            self.m.add(self.load[ti] <= h(self.st.max_hours(term, bulletin_max))).only_enforce_if(
                self.sw["term_limits"])

    def _earlier(self, atom, ti: int, inclusive: bool) -> tuple[bool, list]:
        """(already met by a completed course?, take-variables that would meet it before term ti)."""
        def before(var_by_term):
            return [v for tj, v in var_by_term.items() if tj < ti or (inclusive and tj == ti)]
        if isinstance(atom, CourseAtom):
            if atom.code in self.done:
                return True, []
            return False, before(self.take.get(atom.code, {}))
        if isinstance(atom, LevelAtom):
            def match(cid):
                c = self.cat.courses[cid]
                return c.subject == atom.subject and c.level >= atom.min_level
            if any(match(c) for c in self.done):
                return True, []
            return False, [v for c, vs in self.take.items() if match(c) for v in before(vs)]
        return False, []        # ManualAtom: never assumed

    def _course_rules(self) -> None:
        on = self.sw["course_rules"]
        for cid, per_term in self.take.items():
            course = self.candidates[cid]
            for ti, x in per_term.items():
                for cnf, inclusive in ((course.prereq, False), (course.coreq, True)):
                    for clause in cnf:
                        met, lits = False, []
                        for atom in clause:
                            a_met, a_lits = self._earlier(atom, ti, inclusive)
                            met, lits = met or a_met, lits + [v for v in a_lits if v is not x]
                        if met:
                            continue
                        if lits:
                            self.m.add(sum(lits) >= 1).only_enforce_if([x, on])
                        else:
                            self.m.add(x == 0).only_enforce_if(on)
                if course.min_standing in STANDING_RULES:
                    needed = rule_or(self.cat, STANDING_RULES[course.min_standing], 0)
                    before = h(self.done_hours) + sum(self.load[tj] for tj in range(ti))
                    self.m.add(before >= h(needed)).only_enforce_if([x, on])
        # "Credit not allowed for both"
        seen = set()
        for cid in list(self.take) + list(self.done):
            for other in self.cat.courses[cid].exclusive_with:
                pair = tuple(sorted((cid, other)))
                if pair in seen or other not in self.cat.courses:
                    continue
                seen.add(pair)
                a = 1 if cid in self.done else self.taken.get(cid, 0)
                b = 1 if other in self.done else self.taken.get(other, 0)
                if isinstance(a, int) and isinstance(b, int):
                    continue          # both completed (or neither plannable): nothing to decide
                self.m.add(a + b <= 1).only_enforce_if(on)

    def _have(self, cid: str):
        """1 if completed, the take-sum if plannable, else 0."""
        if cid in self.done:
            return 1
        return self.taken.get(cid, 0)

    def _major(self) -> None:
        on = self.sw["major"]
        excluded = self.cat.excluded_from(self.program.id)
        hours_needs = [n for n in self.pneeds if n.item.kind == "hours"]
        for need in self.pneeds:
            it = need.item
            self.plan.labels[need.key] = need.label
            if it.kind in ("course", "choose"):
                options = [o for o in it.options if not (o in excluded and excluded[o] is None)]
                self.m.add(sum(self._have(o) for o in options) >= it.n).only_enforce_if(on)
                if it.free_text_options:
                    self.plan.notes.append(f"{need.label}: the plan uses a listed course; other options "
                                           f"need advisor approval ({'; '.join(it.free_text_options)}).")
            elif it.kind == "free_text":
                self.plan.notes.append(f"{need.label}: can't be planned automatically; check with your advisor.")
        # Elective-hours rules: each course's hours count toward at most one rule.
        for need in hours_needs:
            terms = []
            for cid in list(self.done) + list(self.take):
                course = self.cat.courses[cid]
                if fits_hours_rule(course, need, self.program.id, self.cat):
                    v = self.m.new_bool_var(f"hrs[{cid},{need.key}]")
                    self.hrs[(cid, need.key)] = v
                    terms.append(h(countable_hours(course, self.program.id, self.cat)) * v)
            self.m.add(sum(terms) >= h(need.item.hours or 0)).only_enforce_if(on)
        for cid in set(c for c, _ in self.hrs):
            used = [v for (c, _), v in self.hrs.items() if c == cid]
            self.m.add(sum(used) <= self._have(cid))

    def _core(self) -> None:
        on = self.sw["core"]
        for need in self.cneeds:
            self.plan.labels[need.id] = need.category.name
            covered = []
            if need.kind != "slot":
                for cid in list(self.done) + list(self.take):
                    if course_counts_for_core(self.cat.courses[cid], need, self.cat, self.ctx.settings,
                                              self.done.get(cid, "wfu")):
                        v = self.m.new_bool_var(f"cov[{cid},{need.id}]")
                        self.cov[(cid, need.id)] = v
                        covered.append(v)
                        self.m.add(v <= self._have(cid))
                if need.kind == "division" and need.count > 1:
                    # Two courses in one division must come from different departments.
                    for subj in {cid.split()[0] for cid, k in self.cov if k == need.id}:
                        self.m.add(sum(v for (c, k), v in self.cov.items()
                                       if k == need.id and c.startswith(subj + " ")) <= 1)
            slots = list(self.slot[need.id].values())
            self.m.add(sum(covered) + sum(slots) >= need.count).only_enforce_if(on)
        # One course counts toward at most one division, and at most one 21CS category.
        for kind in ("division", "21cs"):
            ids = {n.id for n in self.cneeds if n.kind == kind}
            for cid in {c for c, k in self.cov if k in ids}:
                self.m.add(sum(v for (c, k), v in self.cov.items() if c == cid and k in ids) <= 1)

    def _total(self) -> None:
        total = rule_or(self.cat, "total_hours", 120)
        self.m.add(h(self.done_hours) + sum(self.load.values()) >= h(total)).only_enforce_if(self.sw["total"])

    def _objective(self) -> None:
        normal = h(rule_or(self.cat, "normal_hours_per_semester", 15))
        obj = []
        obj.append(1000 * sum(self.load.values()))                    # 1. no extra hours
        for ti, term in enumerate(self.terms):                         # 2. balanced loads
            target = 0 if term.season == "summer" else normal
            dev = self.m.new_int_var(0, h(40), f"dev[{term}]")
            self.m.add(dev >= self.load[ti] - target)
            self.m.add(dev >= target - self.load[ti])
            obj.append(10 * dev)
        obj.append(500 * sum(v for per in self.slot.values() for v in per.values()))   # 3. fewer core slots
        obj.append(sum(5 * ti * v for per in self.slot.values() for ti, v in per.items()))  # 5. core early
        required = {o for n in self.pneeds if n.item.kind in ("course", "choose") for o in n.item.options}
        interests = set(self.st.interests)
        for cid, per_term in self.take.items():
            if cid in interests:
                obj.append(-50 * self.taken[cid])                       # 4. interests
            elif cid not in required:
                obj.append(300 * self.taken[cid])                       # 4. no unneeded courses
            else:
                obj.append(sum(20 * ti * v for ti, v in per_term.items()))   # 5. required courses early
        self.m.minimize(sum(obj))

    # ------------------------------------------------------------------ solve

    def solve(self, time_limit: float, workers: int) -> None:
        self.m.add_assumptions(list(self.sw.values()))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_workers = workers
        solver.parameters.random_seed = 1
        status = solver.solve(self.m)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            self.plan.status = "optimal" if status == cp_model.OPTIMAL else "feasible"
            self._extract(solver)
        elif status == cp_model.INFEASIBLE:
            self.plan.status = "infeasible"
            self.plan.explanations = self._explain(time_limit)
        else:
            self.plan.status = "unknown"
            self.plan.explanations = [f"The solver found no plan within {time_limit:g} seconds. "
                                      "Try a later graduation term or a higher hour limit."]

    def _infeasible_with(self, names: list[str], time_limit: float) -> bool:
        """True if the rule groups in `names` alone (the others switched off) already conflict."""
        self.m.clear_assumptions()
        self.m.add_assumptions([self.sw[n] for n in names])
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_workers = 8
        return solver.solve(self.m) == cp_model.INFEASIBLE

    def _explain(self, time_limit: float) -> list[str]:
        """Find a small set of rule groups that can't all hold together.

        Start with every group on. Switch one group off at a time; if the rest
        still conflict, that group wasn't needed for the conflict, so leave it off.
        """
        conflict = list(SWITCHES)
        for name in list(SWITCHES):
            trial = [n for n in conflict if n != name]
            if trial and self._infeasible_with(trial, time_limit / len(SWITCHES)):
                conflict = trial
        self.m.clear_assumptions()
        self.m.add_assumptions(list(self.sw.values()))
        parts = [SWITCHES[n] for n in conflict]
        return [f"No plan fits by {self.terms[-1]}, given when courses are offered: these rules conflict: "
                + "; ".join(parts) + ". Try a later graduation term, a higher hour limit, or summer terms."]

    def _counts(self, cid: str, solver: cp_model.CpSolver) -> list[str]:
        keys = []
        excluded = self.cat.excluded_from(self.program.id)
        for need in self.pneeds:
            if need.item.kind in ("course", "choose") and cid in need.item.options \
                    and not (cid in excluded and excluded[cid] is None):
                keys.append(need.key)
        keys += [k for (c, k), v in self.hrs.items() if c == cid and solver.value(v)]
        keys += [k for (c, k), v in self.cov.items() if c == cid and solver.value(v)]
        return keys

    def _extract(self, solver: cp_model.CpSolver) -> None:
        defaults_used = []
        for ti, term in enumerate(self.terms):
            tp = TermPlan(term)
            for cid, per_term in self.take.items():
                if ti in per_term and solver.value(per_term[ti]):
                    course = self.candidates[cid]
                    tp.items.append(PlanItem(cid, course.title, course.credits, "course", self._counts(cid, solver)))
                    if all(o.source == "default" for o in course.offerings):
                        defaults_used.append(cid)
            for need in self.cneeds:
                var = self.slot[need.id].get(ti)
                for _ in range(solver.value(var) if var is not None else 0):
                    tp.items.append(PlanItem(need.id, f"{need.category.name} (choose a course)", need.slot_hours,
                                             "core_slot", [need.id]))
            for _ in range(solver.value(self.elec[ti])):
                tp.items.append(PlanItem("ELECTIVE", "Free elective (any course)", ELECTIVE_HOURS, "elective_slot"))
            tp.items.sort(key=lambda i: ({"course": 0, "core_slot": 1, "elective_slot": 2}[i.kind], i.id))
            self.plan.terms.append(tp)
        for cid in self.done:
            self.plan.completed_counts[cid] = self._counts(cid, solver)
        if defaults_used:
            self.plan.notes.append(f"{len(defaults_used)} planned courses use the assumed fall + spring schedule "
                                   "(the Bulletin doesn't say when they run). Confirm with the department.")
        missing = [c for c in self.st.interests if c not in self.take and c not in self.done]
        if missing:
            self.plan.notes.append("Interests the planner can't schedule on its own: " + ", ".join(missing))
