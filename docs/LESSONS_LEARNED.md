# Lessons Learned Log

A running log for the final paper. Each entry covers: what we assumed, what
was true, and what we did about it.

## 2026-10-26: Bulletin inspection (before any code)

1. **Assumption:** the Bulletin lists when each course is offered
   (fall/spring/alternate years).
   **Reality:** none of the CSC or MTH course descriptions say this.
   **Fix:** default to fall + spring, mark it NEEDS HUMAN REVIEW, and add a
   hand-edited overrides file that records its source. *Lesson:* the "source
   of truth" may not contain every fact the algorithm needs, so plan for
   human-provided data.

2. **Assumption:** prerequisites are clean boolean expressions.
   **Reality:** they are English prose with implied subjects ("CSC 231 and
   250"), mixed and/or without parentheses, "or prerequisite override
   required", "any 200-level CSC course", and conditional clauses.
   **Fix:** a small rule-based parser that converts to CNF, flags ambiguous
   cases, and allows manual overrides.

3. **Assumption:** we could use the Bulletin's course-search API.
   **Reality:** `robots.txt` disallows `/course-search/api/` and `/archive/`.
   **Fix:** scrape the static department and Courses A-Z pages instead. Older
   bulletins (for students who entered before 2026) are not available to
   the scraper.

4. **Assumption:** the academic calendar gives dates for every planned term.
   **Reality:** it covers Fall 2026 and Spring 2027 only, has no summer
   session dates, and contains a weekday typo (April 26, 2027 is listed as a
   Wednesday but is a Monday).
   **Fix:** project later years and label them "estimated". Don't trust the
   source blindly.

5. **Assumption:** program rules live in one place.
   **Reality:** the requirements table is structured, but key restrictions
   ("may not be counted toward the major", "no more than 3 hours of CSC 393")
   are written inside individual course descriptions.
   **Fix:** extract them with targeted patterns and flag anything unmatched.

6. Process note: during inspection we accidentally sent a personal email
   address in a scraper User-Agent header. Decision: the scraper uses a
   generic project User-Agent and sends no personal data.

## 2026-10-26: Milestone 0-1 (setup, scraper, validation)

7. **Assumption:** every teammate has a recent Python.
   **Reality:** the default macOS `python3` was 3.9.6, and Homebrew wasn't installed.
   **Fix:** installed Python 3.12 per user with `uv` (no admin rights). The
   README lists both the python.org and `uv` routes.

8. **Assumption:** "or prerequisite override required" always means the
   override is optional.
   **Reality:** some courses say "**and** prerequisite override required"
   (CSC 391/393), or *only* "P-Prerequisite override required". There,
   permission is mandatory.
   **Fix:** two separate flags, `override_allowed` and `override_required`.
   The planner won't auto-schedule courses that need permission.

9. **Assumption:** the word "offered" signals timing.
   **Reality:** most uses are "not counted toward any major *offered by the
   department*".
   **Fix:** a tighter pattern ("offered in fall/spring/alternate…"). Only 2 of
   115 CSC/MTH/STA courses state any timing at all.

10. **Gotcha:** periods inside parentheses and in titles ("Part 1.",
    "B.S.") break naive sentence splitting.
    **Fix:** a splitter that ignores periods inside parentheses. "B.S." still
    splits STA 382's prerequisite; it is flagged for review.

11. **Numbers (2026-27 data):** 115 courses parsed (CSC 42, MTH 49, STA 24).
    80 have prerequisite text, 2 have corequisites. 36 prerequisite items are
    flagged for review (mostly mixed and/or without parentheses), 9 courses
    need department permission, 16 carry the undocumented `(QR)` tag, and 0
    credit-parse failures.

## 2026-10-26: Milestone 2 (data model, prerequisite parser, programs, core)

12. **Team decisions recorded as data, not code.** QR as a school-wide
    requirement, fall + spring as the default offering, and "or binds
    tighter" are kept in `team_decisions.yaml` with a source line. The
    validation report labels them "not from the Bulletin". *Lesson:* keeping
    each rule's source attached makes it easy to explain where a rule came from.

13. **Bug: case-insensitive matching.** The tokenizer matched course codes
    ignoring case, so "and 250" was read as the course "AND 250", and every
    prerequisite with an implied subject failed. **Fix:** course codes are
    matched case-sensitively; only connector words ignore case. Found by
    running the parser on all real prerequisites before writing tests.

14. **Why CNF ("AND of ORs").** "(MTH 121 and 251) or MTH 205" becomes
    (121 or 205) AND (251 or 205). Every clause is one simple constraint for
    the planner, and checking a student is "every clause has a completed
    option".

15. **Parse results:** 82 prerequisite/corequisite sentences. 59 parse
    directly, 15 by the team and/or rule, 3 by a hand override (CSC 385/387
    conditional BIO prerequisites, the STA 112 AP score), and 3 keep a
    manual condition (class standing, "experience with statistical
    computing"). 0 failures.

16. **The Bulletin references a course that doesn't exist:** MTH 355's
    prerequisite lists "MTH 206", which has no entry in Courses A-Z. Flagged
    rather than "fixed". *Lesson:* even the official source has errors.

17. **A self-check from the source itself.** Each program page states
    "Requires a minimum of N hours in computer science". Adding up the
    parsed requirement table gives exactly 42 (B.S.), 33 (B.A.), and 17
    (minor). An automatic check now runs on every build, so a parsing
    mistake in a program table would show up as an error.

18. **Ambiguity that remains:** the B.A. and minor say "Select three hours at
    the 191-level or higher" without naming a subject. We assume CSC (the
    program's department) and flag it NEEDS HUMAN REVIEW.

## 2026-11-03: Milestone 3 (planner + plan checker)

19. **Two independent checks agree.** The solver (CP-SAT) builds the plan;
    `validate_plan.py` re-checks it with plain loops. To test the checker
    we switched off the solver's prerequisite rules on purpose: the checker
    flagged every out-of-order course. *Lesson:* a second, simpler method
    is cheap insurance, and it also works on hand-edited plans.

20. **"Good" is found fast, "provably best" is not.** On the real B.S.
    data the solver has a near-best plan in about 2 seconds but cannot
    prove it optimal in 30 (the remaining gap is tiny ordering
    preferences). Default time limit is 10 s, and the output says when the
    plan wasn't proven best.

21. **"No plan" needs a reason.** Plain-Python pre-checks catch the common
    cases with specific sentences (prerequisite chain too long, not enough
    hours even at the maximum load, a required course that needs
    permission). For the rest, each rule group has an on/off switch; we
    switch groups off one at a time and keep only the ones that still
    conflict. A first version listed all five groups, which told the
    student nothing.

22. **Elective-hours rules must not double-count.** "12 h of CSC 300+"
    can't use CSC 301 or CSC 399, which are required on their own, and in
    the B.A. one course can't fill both "3 h at 200+" and "6 h at 300+".
    The model gives each course to at most one hours rule.

23. **Assumptions we had to make** (in `team_decisions.yaml`, confirm):
    HES 100 is 1 h; a course may count toward the major, one division, one
    21CS category, and QR at once; if a timing deadline already passed
    (e.g. FYS for a junior), the slot goes in the first planned term with a
    note instead of making the plan impossible.

## 2026-11-03: Milestone 4 (degree audit)

24. **A third view of the same rules.** The audit matches courses to
    requirements with simple greedy steps (no solver): done courses first,
    the strictest elective-hours rule picks first, a 21CS category with
    fewer candidates picks first. Running it on a solver plan gives
    "0 remaining", so planner, checker, and audit agree.

25. **Hand-checked sample.** We worked out the junior sample's checklist by
    hand from the Bulletin before running the code; the test compares all
    29 lines. The tricky line: AP credit for CSC 111 counts for the major
    but not for Division V, which is instead met by CSC 112 + MTH 111.

26. **"In progress" courses.** Students plan mid-semester, so profiles now
    have `in_progress:`. The audit shows them separately; the planner
    assumes they are passed and says so in a note.

## 2026-11-03: Milestone 5 (career recommender)

27. **Results for the sample careers.** Machine learning → CSC 375, 374,
    371; cybersecurity → CSC 348, 341, 343; software → CSC 331, 321; data
    science → STA 111, 362, 112, CSC 374. Tests check these on the real data.

28. **Small text details mattered more than the algorithm.**
    - Prerequisite sentences ("P- CSC 201") are cut from descriptions, or
      every course that needs CSC 201 would "match" CSC 201.
    - A tiny stemmer (networks → network, statistical ≈ statistics).
    - Two-word phrases ("neural networks") only from words that are next to
      each other. A first version joined words across a dropped "and",
      producing phrases like "network deep" in the explanations.
    - Catalog boilerplate ("students", "topics", "introduction") is ignored.

29. **The catalog limits the recommender.** "Game developer" finds only
    weak matches (geometry, GPU programming) because the 2026-27 catalog has
    no graphics course. The script says so when the best score is low
    instead of pretending.

30. **Planner link.** Relevance scores (0-100) lower the cost of an
    elective in the solver's objective, so career-relevant courses fill the
    "12 h of CSC 300+" rule. Explicit `interests` still win.

31. **Not done (cut list #1):** the optional LLM rewrite of the reason
    sentence. The template reasons are clear enough for the demo.

## 2026-11-03: All departments and programs (scope change)

The team asked for every major and minor for the demo. Plan: time-boxed to
11/12, College majors/minors plus the School of Business; certificates,
concentrations, and self-designed majors are out.

32. **Discovery instead of a hand-typed list.** Three index pages (departments,
    business programs, Courses A-Z) list everything: 57 departments, 132
    program pages, 78 subjects, 2,551 courses. About 330 polite requests;
    8 failed on network hiccups and worked on a re-run (the cache makes
    re-runs cheap).

33. **Prerequisites across all departments.** Of 802 prerequisite
    sentences, 218 failed with the CS-only parser. A cleanup step rewrites
    about 15 common styles first ("A / B", "with a C or better", "or
    equivalent", "200-level course", "junior or senior standing",
    "P or C- X", "A, B, or C", ranges like "SPA 310 - SPA 319", "any two of
    the following"). Now 10 fail; they are flagged, never dropped.
    *Lesson:* look at all the failures before writing rules; 15 patterns
    covered 95% of them.

34. **Program tables vary much more than course text.** Biology's B.S. has a
    "Choose three of the following" row that *links* to a list further down
    the page; Philosophy says "one course from each of the following groups"
    with each group in its own table; Engineering includes a sample
    schedule table that must not be read as requirements. Each of these
    first produced a confidently wrong reading (e.g. all 14 options becoming
    required courses), caught only by running the planner on every program.

35. **Loose readings are worse than "advisor review".** A generic rule
    ("Select 24 hours in Greek" -> 24 h of GRK) helped many programs, but
    its first version read "two 200 or 300 level courses" as 900 hours and
    "Art History" as the History department. We now reject rows with extra
    conditions ("including at least...", "must", "or one ...") and leave
    them for an advisor. Every automatic reading is flagged "Confirm" in the
    validation report.

36. **One unplannable course shouldn't sink the plan.** Required courses that
    need permission or a human-checked prerequisite (e.g. CHM 390) used to
    make the whole plan "impossible". Now the planner plans everything else
    and adds a note; the audit shows that line as remaining.

37. **Explanations name the requirement.** Each major requirement has its own
    on/off switch, so "no plan" says e.g. "the requirement 'BEM 388'
    conflicts with prerequisites" instead of "the major conflicts". That is
    how we found BEM 211's corequisite "BEM 287 (For BEM majors only)" being
    applied to finance students.

38. **Divisions for every subject.** Subject names from Courses A-Z are
    matched to the division department lists (e.g. Biology -> Division V);
    three names that differ (Religion, WGS, Politics) are in
    team_decisions.yaml. Subjects that match nothing don't count toward a
    division automatically; the plan uses a generic slot instead.

39. **Advisor knowledge the Bulletin pages don't show.** A teammate pointed
    out that business majors can't take School of Business courses until
    they're admitted in sophomore spring. That admission page isn't one we
    scrape, so it's a team rule in team_decisions.yaml with its source. We
    first also required the admission prerequisites (ACC 111, BEM 201) to be
    finished *before* sophomore spring, but the Bulletin's own prerequisites
    make that impossible (ACC 111 needs sophomore standing, and BEM 201 needs
    ACC 111), so we dropped it. A rule that sounds right can conflict with the data.

40. **Several programs at once.** Requirement keys already started with the
    program id ("CSC-BS:0.3"), so a double major or minor was mostly a loop
    over programs in the planner, checker, audit, and recommender. Within
    one program a course fills at most one elective-hours rule; across
    programs it may count for each. Forbidden pairs are copied by hand from
    the department pages.

41. **"No plan in time" is not "impossible".** Finance + Computer Science
    minor in 8 semesters needs about 137 hours against a 136-hour maximum.
    The solver can't prove that within 2 minutes, so it says "no plan found
    in time" and suggests more semesters, not "impossible".

42. **Study abroad as slots, not courses.** We don't have abroad course
    lists, so an abroad semester is planned with 3-hour "abroad course"
    slots that say what they count toward. The Bulletin gives the general
    rules (30-hour outside-credit limit, no 21CS from transfer credit,
    senior year at Wake Forest) and a few department limits (Sociology 6 h,
    Spanish 3 h from affiliate programs). Most College departments say
    nothing, so the 6-hour default is a labeled team assumption.
