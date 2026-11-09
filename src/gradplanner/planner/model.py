"""The planner: places courses into semesters with Google OR-Tools CP-SAT.

CP-SAT is a constraint solver. We describe the problem with yes/no variables
and rules, and the solver finds an assignment that follows every rule while
scoring as well as possible on our objective. We never write the search
ourselves, so a plan it returns follows the rules by construction (and
validate_plan.py checks again, independently).

Variables
  take[c, t]   1 if course c is placed in term t (only created when c is offered in t)
  slot[k, t]   number of generic core slots of category k in term t ("Division I: choose a course");
               in a study-abroad term, a course taken abroad that counts for k
  abr[n, t]    number of 3-hour abroad courses counting toward elective-hours rule n in abroad term t
  elec[t]      number of 3-hour free-elective slots in term t
  hrs[c, n]    1 if course c's hours count toward elective-hours rule n ("12 h of CSC 300+")
  cov[c, k]    1 if course c counts toward core category k

Rules (each group has an on/off switch, used to explain "no valid plan")
  course rules   each course at most once; prerequisites in an earlier term; corequisites
                 in the same or earlier term; class standing; "credit not allowed for both"
  term limits    hours per term <= the student's maximum (never above the Bulletin's 17)
  major/minor    every required course; every "choose" list; every elective-hours rule, for
                 each of the student's majors and minors (one switch per requirement, so an
                 explanation can name it)
  business       School of Business courses: business majors not before admission
                 (sophomore spring) except admission prerequisites; other students only
                 when one of their programs requires the course
  core           every core category is covered by a course or a slot; timing (FYS/WRI in
                 year 1, HES by the end of year 2); one language course per term
  total hours    at least 120 hours by graduation
  study abroad   no catalog courses in an abroad term; a full-time load there; abroad credit
                 toward each major/minor within its limit (business 6 h; Sociology 6 h; ...);
                 affiliate (non-Wake) programs: transfer-credit limit of 30 h, no 21CS

Objective, most important first
  1. no more hours than needed       2. loads close to the normal 15 h
  3. cover core with courses already in the plan instead of extra slots
  4. no unneeded catalog courses; prefer the student's interests, then courses
     that match their career goal (relevance scores 0-100 from the recommender)
  5. required courses and core slots as early as possible
  When re-planning (`previous` given), between 1 and 2: keep courses and slots in
  the semesters they were already in, so only what has to change moves.

Assumptions: a course may count toward the major, one division, one 21CS
category, and QR at the same time. With two majors (or a major and minors), a
course may count toward each program; several departments say so explicitly,
and the Bulletin has no general limit. Pairs the Bulletin forbids (e.g. Biology
with Neuroscience) are refused before planning.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ortools.sat.python import cp_model

from gradplanner.models import Catalog, Course, CourseAtom, LevelAtom
from gradplanner.planner.plan import Plan, PlanItem, TermPlan
from gradplanner.planner.precheck import precheck, unplannable_needs
from gradplanner.planner.requirements import (
    ABROAD_COURSE_HOURS, PlannerSettings, abroad_category_ok, abroad_problems, abroad_program_cap,
    admission_courses, business_reason, core_needs, countable_hours,
    course_counts_for_core, fits_hours_rule, is_business_student, load_settings, needed_courses, program_needs,
    outside_hours_toward, program_problems, rule_or, schedulable_reason, student_needs,
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
    "core": "the Core Curriculum requirements and their timing",
    "total": "the 120-hour graduation total",
    "abroad": "the study abroad plan (full-time load and limits on abroad credit)",
}


TOP_RELEVANT = 25        # career matches the planner may consider as free electives


def relevant_courses(catalog: Catalog, program, student: StudentProfile,
                     relevance: dict[str, int] | None = None) -> set[str]:
    """Courses worth giving the solver: the program's courses, courses that can fill its
    elective-hours rules, the student's interests, the best career matches, and everything
    those need as prerequisites or corequisites. With thousands of courses in the catalog,
    this keeps the model small; core requirements not met by these courses become slots."""
    needs = program_needs(program)
    seeds = {o for n in needs if n.item.kind in ("course", "choose") for o in n.item.options}
    for n in needs:
        if n.item.kind == "hours" and n.item.options:
            seeds |= set(n.item.options)
        elif n.item.kind == "hours":
            seeds |= {cid for cid, c in catalog.courses.items()
                      if c.subject in (n.item.subject or "").split("|") and c.level >= (n.item.min_level or 0)}
    seeds |= set(student.interests)
    if relevance:
        seeds |= {cid for cid, sc in sorted(relevance.items(), key=lambda kv: -kv[1])[:TOP_RELEVANT] if sc > 0}
    out, todo = set(), [c for c in seeds if c in catalog.courses]
    while todo:
        cid = todo.pop()
        if cid in out:
            continue
        out.add(cid)
        course = catalog.courses[cid]
        for clause in course.prereq + course.coreq:
            for atom in clause:
                if isinstance(atom, CourseAtom) and atom.code in catalog.courses:
                    todo.append(atom.code)
                elif isinstance(atom, LevelAtom):
                    todo += [c for c, co in catalog.courses.items()
                             if co.subject == atom.subject and co.level >= atom.min_level]
    return out


def h(hours: float) -> int:
    return int(round(hours * SCALE))


@dataclass
class _Ctx:
    catalog: Catalog
    student: StudentProfile
    settings: PlannerSettings
    terms: list[Term]


def plan_for_student(catalog: Catalog, student: StudentProfile, settings: PlannerSettings | None = None,
                     time_limit: float = 10.0, workers: int = 8, relevance: dict[str, int] | None = None,
                     previous: Plan | None = None) -> Plan:
    """Build the best plan for `student`, or explain why none exists.

    `relevance` ({course_id: 0-100}, from the recommender) makes the planner
    prefer career-relevant courses when it picks electives.
    `previous` (an earlier plan for this student) makes it keep that plan's
    choices where it still can, for re-planning after a semester or a change.
    """
    settings = settings or load_settings(catalog)
    unknown = [p for p in student.programs if p not in catalog.programs]
    if unknown:
        raise ValueError(f"unknown program(s) {unknown}; known: {sorted(catalog.programs)}")
    ctx = _Ctx(catalog, student, settings, student.plan_terms())
    plan = Plan(student.name, " + ".join(student.programs), "unknown")
    if problems := program_problems(catalog, student, settings) + abroad_problems(student):
        plan.status, plan.explanations = "infeasible", problems
        return plan

    blocking, notes = precheck(catalog, student, settings, ctx.terms)
    plan.notes += notes
    if blocking:
        plan.status = "infeasible"
        plan.explanations = blocking
        return plan

    if student.abroad:
        plan.notes += settings.abroad.notes
        if any(a.program == "affiliate" for a in student.abroad):
            plan.notes.append("Credit from an affiliate (non-Wake Forest) program is transfer credit: it isn't in "
                              "your GPA and counts toward the 30-hour limit on outside credit.")
    if is_business_student(catalog, student, settings):
        plan.notes.append("School of Business courses are planned from sophomore spring on (after admission to "
                          "the School of Business); only the admission prerequisites come earlier.")
    builder = _Builder(ctx, plan, relevance or {}, previous)
    builder.build()
    builder.solve(time_limit, workers)
    return plan


class _Builder:
    def __init__(self, ctx: _Ctx, plan: Plan, relevance: dict[str, int], previous: Plan | None = None) -> None:
        self.ctx, self.plan, self.relevance, self.previous = ctx, plan, relevance, previous
        self.cat, self.st, self.terms = ctx.catalog, ctx.student, ctx.terms
        self.programs = [self.cat.programs[p] for p in self.st.programs]
        self.m = cp_model.CpModel()
        self.sw = {name: self.m.new_bool_var(f"switch_{name}") for name in SWITCHES}
        self.need_sw: dict[str, cp_model.IntVar] = {}       # one switch per major/minor requirement

        # Completed courses we know about, and hours already earned.
        self.done: dict[str, str] = {}
        for c in self.st.finished:
            if c.course in self.cat.courses:
                self.done[c.course] = c.source
            else:
                plan.notes.append(f"{c.course} is not in our catalog; list it under outside_courses instead.")
        if self.st.in_progress:
            plan.notes.append("Assumes you pass your current courses: " + ", ".join(self.st.in_progress) + ".")
        self.done_hours = (sum(self.cat.courses[c].credits for c in self.done)
                           + sum(o.hours for o in self.st.outside_courses))

        self.admission = admission_courses(self.cat)
        self.candidates: dict[str, Course] = {}
        seeds = set().union(*(relevant_courses(self.cat, p, self.st, relevance) for p in self.programs))
        for cid in seeds:
            course = self.cat.courses[cid]
            if cid not in self.done and schedulable_reason(course) is None:
                self.candidates[cid] = course

        self.take: dict[str, dict[int, cp_model.IntVar]] = {}
        self.taken: dict[str, cp_model.LinearExpr] = {}
        self.slot: dict[str, dict[int, cp_model.IntVar]] = {}
        self.elec: dict[int, cp_model.IntVar] = {}
        self.load: dict[int, cp_model.IntVar] = {}
        self.hrs: dict[tuple[str, str], cp_model.IntVar] = {}
        self.cov: dict[tuple[str, str], cp_model.IntVar] = {}
        self.pneeds = student_needs(self.cat, self.st)
        self.required = needed_courses(self.cat, self.st)
        self.cneeds = [n for n in core_needs(self.cat, self.st, ctx.settings) if n.count > 0]
        self.abroad = {ti: a for ti, t in enumerate(self.terms) if (a := self.st.abroad_in(t))}
        self.skipped = unplannable_needs(self.cat, self.st)
        self.abr: dict[tuple[str, int], cp_model.IntVar] = {}
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
                        for ti, t in enumerate(self.terms) if course.is_offered(t.season, t.year)
                        and ti not in self.abroad
                        and business_reason(course, t, self.st, self.cat, self.ctx.settings, self.admission) is None}
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
                if ti in self.abroad and not abroad_category_ok(need.category, self.ctx.settings,
                                                                self.abroad[ti].program == "affiliate"):
                    continue
                self.slot[need.id][ti] = self.m.new_int_var(0, 1 if need.one_per_term else need.count,
                                                            f"slot[{need.id},{term}]")
        for need in self.pneeds:
            if need.item.kind == "hours" and need.key not in self.skipped:
                for ti in self.abroad:
                    self.abr[(need.key, ti)] = self.m.new_int_var(0, 5, f"abr[{need.key},{self.terms[ti]}]")
        bulletin_max = rule_or(self.cat, "max_hours_per_semester", 17)
        for ti, term in enumerate(self.terms):
            expr = h(ELECTIVE_HOURS) * self.elec[ti]
            expr += sum(h(ABROAD_COURSE_HOURS) * v for (_, tj), v in self.abr.items() if tj == ti)
            expr += sum(h(self.candidates[c].credits) * v[ti] for c, v in self.take.items() if ti in v)
            expr += sum(h(n.slot_hours) * self.slot[n.id][ti] for n in self.cneeds if ti in self.slot[n.id])
            self.load[ti] = self.m.new_int_var(0, h(40), f"load[{term}]")
            self.m.add(self.load[ti] == expr)
            self.m.add(self.load[ti] <= h(self.st.max_hours(term, bulletin_max))).only_enforce_if(
                self.sw["term_limits"])
            if ti in self.abroad:
                self.m.add(self.load[ti] >= h(self.ctx.settings.abroad.min_hours)).only_enforce_if(self.sw["abroad"])
        # Transfer-credit limit: AP/transfer already earned + affiliate abroad hours.
        affiliate = [self.load[ti] for ti, a in self.abroad.items() if a.program == "affiliate"]
        if affiliate:
            earned = (sum(self.cat.courses[c.course].credits for c in self.st.finished
                          if c.source != "wfu" and c.course in self.cat.courses)
                      + sum(o.hours for o in self.st.outside_courses if o.source != "wfu"))
            limit = rule_or(self.cat, "max_outside_hours", 30)
            self.m.add(h(earned) + sum(affiliate) <= h(limit)).only_enforce_if(self.sw["abroad"])

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

    def _need_switch(self, key: str):
        if key not in self.need_sw:
            self.need_sw[key] = self.m.new_bool_var(f"switch_{key}")
        return self.need_sw[key]

    def _label(self, need) -> str:
        if len(self.programs) == 1:
            return need.label
        return f"{self.cat.programs[need.program_id].name} – {need.label}"

    def _major(self) -> None:
        hours_needs = [n for n in self.pneeds if n.item.kind == "hours"]
        skipped = self.skipped                               # left for the advisor (noted by precheck)
        for need in self.pneeds:
            it = need.item
            excluded = self.cat.excluded_from(need.program_id)
            self.plan.labels[need.key] = self._label(need)
            if need.key in skipped:
                continue
            if it.kind in ("course", "choose"):
                options = [o for o in it.options if not (o in excluded and excluded[o] is None)]
                self.m.add(sum(self._have(o) for o in options) >= it.n).only_enforce_if(self._need_switch(need.key))
                if it.free_text_options:
                    self.plan.notes.append(f"{self._label(need)}: the plan uses a listed course; other options "
                                           f"need advisor approval ({'; '.join(it.free_text_options)}).")
            elif it.kind == "free_text":
                self.plan.notes.append(f"{self._label(need)}: can't be planned automatically; check with your advisor.")
            elif it.kind == "note":
                self.plan.notes.append(f"Program note: {self._label(need)}"
                                       + (f" ({it.needs_review})" if it.needs_review else ""))
        # Elective-hours rules: within one program, a course's hours count toward at most one rule.
        for need in hours_needs:
            if need.key in skipped:
                continue
            terms = []
            for cid in list(self.done) + list(self.take):
                course = self.cat.courses[cid]
                if fits_hours_rule(course, need, need.program_id, self.cat):
                    v = self.m.new_bool_var(f"hrs[{cid},{need.key}]")
                    self.hrs[(cid, need.key)] = v
                    terms.append(h(countable_hours(course, need.program_id, self.cat)) * v)
            terms += [h(ABROAD_COURSE_HOURS) * v for (k, _), v in self.abr.items() if k == need.key]
            already = h(outside_hours_toward(self.st, need))          # e.g. credit already earned abroad
            self.m.add(sum(terms) + already >= h(need.item.hours or 0)).only_enforce_if(self._need_switch(need.key))
        # Limits on abroad credit toward each program (all abroad terms; affiliate terms alone).
        for prog in self.programs:
            mine = [(ti, v) for (k, ti), v in self.abr.items() if k.startswith(prog.id + ":")]
            if not mine:
                continue
            for affiliate_only in (False, True):
                cap = abroad_program_cap(self.cat, prog.id, self.ctx.settings, affiliate_only)
                vs = [v for ti, v in mine if not affiliate_only or self.abroad[ti].program == "affiliate"]
                if vs:
                    self.m.add(h(ABROAD_COURSE_HOURS) * sum(vs) <= h(cap)).only_enforce_if(self.sw["abroad"])
        for cid, pid in {(c, k.split(":", 1)[0]) for c, k in self.hrs}:
            used = [v for (c, k), v in self.hrs.items() if c == cid and k.startswith(pid + ":")]
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
        obj += self._stability()
        obj.append(500 * sum(v for per in self.slot.values() for ti, v in per.items()   # 3. fewer core slots
                             if ti not in self.abroad))
        # Abroad: divisionals and major/minor credit are what students go for; plain electives last.
        obj.append(-100 * sum(self.abr.values()))
        obj.append(200 * sum(self.elec[ti] for ti in self.abroad))
        obj.append(sum(5 * ti * v for per in self.slot.values() for ti, v in per.items()))  # 5. core early
        required = self.required
        interests = set(self.st.interests)
        for cid, per_term in self.take.items():
            if cid in interests:
                obj.append(-50 * self.taken[cid])                       # 4. interests
            elif cid not in required:
                score = max(0, min(100, self.relevance.get(cid, 0)))
                obj.append((300 - 2 * score) * self.taken[cid])         # 4. no unneeded courses; career fit
            else:
                obj.append(sum(20 * ti * v for ti, v in per_term.items()))   # 5. required courses early
        self.m.minimize(sum(obj))

    KEEP_SAME_TERM = 3000      # below one extra hour (10 tenths x 1000), above balance and preferences
    KEEP_IN_PLAN = 1500

    def _stability(self) -> list:
        """Rewards (negative costs) for keeping the previous plan's choices."""
        if not self.previous:
            return []
        index = {t: ti for ti, t in enumerate(self.terms)}
        obj = []
        for tp in self.previous.terms:
            ti = index.get(tp.term)
            for item in tp.items:
                if item.kind == "course" and item.id in self.take:
                    per = self.take[item.id]
                    obj.append(-self.KEEP_IN_PLAN * self.taken[item.id])
                    if ti in per:
                        obj.append(-self.KEEP_SAME_TERM * per[ti])
            if ti is None:
                continue
            for cat, n in Counter(i.id for i in tp.items if i.kind == "core_slot").items():
                var = self.slot.get(cat, {}).get(ti)
                if var is not None:
                    kept = self.m.new_int_var(0, n, f"kept[{cat},{tp.term}]")
                    self.m.add(kept <= var)
                    obj.append(-self.KEEP_SAME_TERM * kept)
        return obj

    # ------------------------------------------------------------------ solve

    def solve(self, time_limit: float, workers: int) -> None:
        self.m.add_assumptions(self._all_switches())
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
            several = " This combination of programs may need more semesters." if len(self.programs) > 1 else ""
            self.plan.explanations = [f"The solver found no plan within {time_limit:g} seconds.{several} "
                                      "Try a later graduation term, a higher hour limit, or summer terms."]

    def _all_switches(self) -> list:
        return list(self.sw.values()) + list(self.need_sw.values())

    def _infeasible_with(self, names: list[str], time_limit: float) -> bool:
        """True if the rule groups/requirements in `names` alone (the rest switched off) already conflict."""
        lits = [self.sw[n] if n in self.sw else self.need_sw[n] for n in names]
        self.m.clear_assumptions()
        self.m.add_assumptions(lits)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_workers = 8
        return solver.solve(self.m) == cp_model.INFEASIBLE

    def _explain(self, time_limit: float) -> list[str]:
        """Find a small set of rules that can't all hold together.

        Start with every rule group and every major requirement switched on. Switch one
        off at a time; if the rest still conflict, it wasn't needed for the conflict, so
        leave it off. What remains names the requirements and rule groups that clash.
        """
        conflict = list(SWITCHES) + list(self.need_sw)
        budget = max(1.0, time_limit / max(1, len(conflict)))
        for name in list(conflict):
            trial = [n for n in conflict if n != name]
            if trial and self._infeasible_with(trial, budget):
                conflict = trial
        self.m.clear_assumptions()
        self.m.add_assumptions(self._all_switches())
        parts = [SWITCHES[n] if n in SWITCHES else f"the requirement '{self.plan.labels.get(n, n)}'"
                 for n in conflict]
        return [f"No plan fits by {self.terms[-1]}, given when courses are offered: these rules conflict: "
                + "; ".join(parts) + ". Try a later graduation term, a higher hour limit, or summer terms."]

    def _counts(self, cid: str, solver: cp_model.CpSolver) -> list[str]:
        keys = []
        for need in self.pneeds:
            excluded = self.cat.excluded_from(need.program_id)
            if need.item.kind in ("course", "choose") and cid in need.item.options \
                    and not (cid in excluded and excluded[cid] is None):
                keys.append(need.key)
        keys += [k for (c, k), v in self.hrs.items() if c == cid and solver.value(v)]
        keys += [k for (c, k), v in self.cov.items() if c == cid and solver.value(v)]
        return keys

    def _abroad_term(self, ti: int, term: Term, solver: cp_model.CpSolver) -> TermPlan:
        a = self.abroad[ti]
        tp = TermPlan(term, abroad=a.location or ("Wake Forest program" if a.program == "wfu" else "affiliate program"))
        for need in self.cneeds:
            var = self.slot[need.id].get(ti)
            for _ in range(solver.value(var) if var is not None else 0):
                tp.items.append(PlanItem("ABROAD", f"Abroad course: {need.category.name}", need.slot_hours,
                                         "abroad", [need.id]))
        for (key, tj), var in self.abr.items():
            if tj == ti:
                for _ in range(solver.value(var)):
                    tp.items.append(PlanItem("ABROAD", f"Abroad course toward {self.plan.labels.get(key, key)}",
                                             ABROAD_COURSE_HOURS, "abroad", [key]))
        for _ in range(solver.value(self.elec[ti])):
            tp.items.append(PlanItem("ABROAD", "Abroad course: elective", ELECTIVE_HOURS, "abroad"))
        return tp

    def _extract(self, solver: cp_model.CpSolver) -> None:
        defaults_used = []
        for ti, term in enumerate(self.terms):
            tp = TermPlan(term)
            if ti in self.abroad:
                self.plan.terms.append(self._abroad_term(ti, term, solver))
                continue
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
