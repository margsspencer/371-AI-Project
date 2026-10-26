# Graduation Calendar Planner: Design (pre-code)

Status: draft for team review, written 2026-10-26. Source of truth: the
2026-2027 WFU Undergraduate Bulletin (https://bulletin.wfu.edu/undergraduate/).

Decisions so far (see also `data/overrides/team_decisions.yaml`):
- **QR (Quantitative Reasoning):** a school-wide requirement of at least one
  QR-tagged course (team, 2026-10-26; not on the scraped degree page).
- **Offerings:** every course is offered fall + spring unless the Bulletin or
  `offerings.csv` says otherwise (team-approved default, 2026-10-26).
- **Mixed and/or with no parentheses:** "or" binds tighter, so every course
  joined by "or" is an option (team, 2026-10-26).
- **LLM:** optional. The recommender works offline by default; an LLM may
  rewrite the explanation sentence if an API key is set.
- **Core Curriculum:** placeholder slots that the audit tracks. The planner
  does not pick specific core courses.
- **Missing offering pattern:** assume fall + spring, mark it NEEDS HUMAN
  REVIEW, and allow hand-entered overrides.
- **Team:** 2-3 people who are comfortable with Python, so a constraint-solver
  library (OR-Tools) is fine.

---

## 1. How the Bulletin is structured (what I found)

The site runs on **CourseLeaf**, a common catalog system, so the HTML is
regular and parseable. `robots.txt` blocks `/course-search/api/`,
`/archive/`, `/search/`, and others. We will **not** use the course-search
API. Instead we scrape the static department pages and `/courses-az/<subj>/`,
which are allowed.

### 1a. CS department page (`/undergraduate/departments-programs/computer-science/`)
- Tabs: Overview, Programs, Courses, Faculty. The courses are also on
  `/courses-az/csc/` (42 course blocks).
- Each course is a `div.courseblock` with:
  - `p.courseblocktitle`: `CSC 201. Data Structures and Algorithms. (3 h)`
  - `p.courseblockdesc`: description, then the prerequisite sentence
    (`P-...`), then tags in parentheses at the end: `(D)` = counts for a
    divisional requirement, `(QDA)`, `(QR)`, and so on.
  - Course codes inside the text are links (`a.bubblelink.code`), which helps
    us find them.
- Overview text contains rules that matter for planning, written as prose:
  a 2.0 GPA in major courses; AP 4/5 gives credit for CSC 111; take
  CSC 111 + MTH 117 in year 1; how double-major courses can be shared.

### 1b. Programs (`.../bs-computer-science/`, `.../ba-computer-science/`, `.../minor-computer-science/`)
Each program has one requirements table (`table.sc_courselist`). Row types:
| Row type | Example | How we model it |
|---|---|---|
| Header row (`areaheader`) | "Required Major Courses", "Co-Requirements" | a RequirementGroup |
| Single course | `CSC 201 \| Data Structures and Algorithms \| 3` | a required course |
| `orclass` row | `or MTH 205` under `MTH 121` | an OR alternative |
| Rule row | "Select at least 12 hours in computer science courses at the 300-level or higher \| 12" | a level-based elective rule (subject, min level, hours) |
| "Select one of the following:" + list | B.A. math co-requirement | choose-N from a list |
| Free-text row | "Any statistics course approved by the Computer Science Department" | **NEEDS HUMAN REVIEW** (cannot be computed) |

B.S. summary: 42 CSC hours (111, 112, 201, 231, 250, 251, 270, 280, 301,
399 + 12 h at 300+), plus MTH 112, MTH 117, and (MTH 121 or MTH 205).
B.A. and minor are shorter tables with the same structure.

### 1c. Degree requirements (`/undergraduate/requirements-degrees/`)
- 120 total hours. A student graduates under the bulletin of the year they
  matriculated, but the major's requirements are the ones in effect when
  they declared it.
- **Basic:** FYS 100 (first year), WRI 111 or WRI 109 + 110 (first year),
  one 200-level foreign language course, HES 100 (before the end of year 2).
- **Divisional:** a table of 5 divisions with the number of courses each:
  I = 2, II = 1, III = 1, IV = 2, V = 2. Restrictions: two courses in the
  same division must come from different departments; one course cannot
  count for two divisions; courses must carry the (D) tag; AP/IB cannot
  satisfy divisionals.
- **21st Century Stewardship (21CS):** POR, SWC, EI, QDA, one course each,
  described only in prose. AP and transfer credit cannot count. A course may
  carry several tags, but the student counts it toward only one.
- Except for HES 100, only courses of 3+ hours count toward basic and
  divisional requirements.

### 1d. Other rules found in Procedures
- Normal load is 15 h. **The maximum without permission is 17 h.**
  (`/undergraduate/procedures/advising-registration/`)
- Class standing: sophomore at 25 h, junior at 55 h, senior at 87 h.
- The senior year must be taken in Wake Forest programs. At most 30 hours may
  come from outside sources after enrolling.

### 1e. Academic calendar (`/undergraduate/calendar/`)
- Only Fall 2026 and Spring 2027 are listed: classes begin and end, exams,
  breaks. **There are no summer session dates and no future years.**

### 1f. What will be hard to parse (and our plan)
| # | Problem | Plan |
|---|---|---|
| 1 | **The Bulletin has no offering pattern (fall/spring/alternate) for any CSC or MTH course I checked.** | Default every course to fall + spring and mark it NEEDS HUMAN REVIEW. Add a hand-edited `data/overrides/offerings.csv` (for example, from the CS department's posted rotation) that records its source. Edge-case tests use synthetic courses. |
| 2 | The subject is implied in prerequisites: `P-CSC 231 and 250`, `MTH 121 or 205`. | Tokenizer carries the last-seen subject forward. |
| 3 | Mixed and/or precedence: `P-CSC 201 and 270, MTH 112 and MTH 121 or 205`. | Commas and semicolons split AND clauses. Inside a clause, "or" between bare codes binds tighter (X and (Y or Z)), which matches every MTH/CSC example I read. Every clause with both "and" and "or" and no parentheses is flagged for review. |
| 4 | `or prerequisite override required` | Removed from the logic and stored as a flag `override_allowed=True`. The planner never relies on overrides. |
| 5 | Prerequisites that aren't course codes: `any 200-level CSC course`, `Prerequisite override required` (only), conditional (`if taken as BIO 385`). | Special atoms (`LevelAtom(subject, min_level)`, `ManualAtom`). Conditional ones are flagged NEEDS HUMAN REVIEW. |
| 6 | Course exclusions are hidden in descriptions: "May not be counted towards the CS major", "Not to be counted toward the B.S.", "No more than three hours may be counted toward a CS major". | Regex extracts them into `course_program_exclusion` rows; anything unmatched is flagged. |
| 7 | "Credit not allowed for both CSC 352 and MTH 326" | Stored as mutual-exclusion pairs. |
| 8 | Variable credit `(1-3 h)` | Store min/max. The planner uses min unless the student sets it. |
| 9 | Tags such as `(QR)` that don't appear on the degree page's 21CS list. | Stored raw, and unknown tags are flagged. |
| 10 | The calendar has one year only, plus a typo ("April 26, Wednesday"; 2027-04-26 is a Monday). | Use the real 2026-27 dates. Later years are projected and labeled "estimated". Summer dates: NEEDS HUMAN REVIEW. |
| 11 | No AP equivalency table found in the Bulletin (only "AP 4/5 → CSC 111" on the CS page). | The student enters AP/transfer credit as course equivalents. The audit enforces "AP doesn't count for divisional or 21CS". |
| 12 | Catalog year: test users who entered 2023-2025 follow older bulletins, and `/archive/` is blocked by robots.txt. | The MVP uses the 2026-27 rules for everyone, with a visible note in the UI. The `bulletin_year` column allows adding older years later (for example, from the archived PDFs, downloaded by hand). |

---

## 2. Architecture

```
            ┌──────────────┐  raw HTML cache  ┌──────────────┐
 Bulletin → │  scraper/    │ ───────────────▶ │ parser/      │──▶ SQLite (data/bulletin.db)
            │ fetch+cache  │  data/raw/2026-27│ courses, prereq   + overrides/*.csv
            └──────────────┘                  │ programs, core│
                                              └──────┬───────┘
                                                     │ validate.py → report of parse failures
        ┌─────────────────────┬──────────────────────┼─────────────────────┐
        ▼                     ▼                      ▼                     ▼
  recommender/           planner/               audit/                export/
  TF-IDF + career        OR-Tools CP-SAT        requirement checklist  CSV + .ics
  keyword profiles       + independent           (satisfied / in
  (+ optional LLM)       plan validator          progress / remaining)
        └──── relevance ────▶ │                      │
                              ▼                      ▼
                         ui/ (Streamlit): profile → recommendations → plan(s) → diff → audit → export
```

Folder layout:
```
src/gradplanner/
  scraper/      fetch.py (polite fetch + cache), sources.py (URL list)
  parser/       courses.py, prereq.py, programs.py, core.py, calendar.py
  db/           schema.sql, models.py (dataclasses), repo.py (queries)
  planner/      model.py (CP-SAT), validate_plan.py, explain.py, diff.py
  recommender/  tfidf.py, careers.yaml (pick-list → keywords), llm.py (optional)
  audit/        audit.py
  export/       csv_export.py, ics_export.py
  ui/           app.py (Streamlit)
scripts/        scrape.py, build_db.py, validate_data.py
data/raw/       cached HTML (git-ignored)   data/overrides/  hand fixes (committed)
samples/        fake students (YAML)
tests/          pytest
docs/           DESIGN.md, LESSONS_LEARNED.md
```

### Tech stack (all free, all local)
| Need | Choice | Why |
|---|---|---|
| Python | 3.11+ (your laptop has 3.9.6; install 3.12 via python.org or `brew install python@3.12`) | Required by the project brief |
| Scraping | `requests` + `beautifulsoup4` (built-in `html.parser`) | Simple and well documented. Nothing to compile. |
| Storage | SQLite (Python's built-in `sqlite3`) | One file, no server, easy to inspect with DB Browser |
| Planner | **OR-Tools CP-SAT** | Free. It states the problem as constraints, so correctness comes from the model rather than our search code. It can also explain infeasibility (see below). |
| Recommender | `scikit-learn` TF-IDF + cosine similarity | Explainable: "matched words: *neural, learning, data*". Light install (no PyTorch). |
| Optional LLM | Read from env var, off by default | Only rephrases text we provide. Never selects courses. |
| UI | Streamlit | Pure Python, fast to build |
| Export | `csv` (built-in) + `icalendar` | Simple |
| Tests | `pytest` | Standard |

### Why this is realistic for beginners
- Each folder does one thing and can be tested alone. A teammate can own the
  scraper while another owns the planner.
- The "hard AI" part (scheduling) is handed to a solver. We write about 15
  short constraint rules in plain English plus code, not a custom search
  algorithm.
- A second, simple validator (plain Python loops) re-checks every plan the
  solver returns. This is what makes "provably valid" easy to explain in the
  paper: two independent methods agree.

### Planner model (plain English)
- Terms: the student's remaining semesters (F/S, plus optional summer), up to
  the target graduation term.
- Variable `take[c,t]` = 1 if course *c* is placed in term *t*.
- Rules:
  1. Each course at most once (unless it's repeatable). Completed courses are
     fixed in the past.
  2. **Prerequisites** are stored in CNF form ("AND of ORs"). For each OR
     clause, at least one option must be done in an *earlier* term (or
     already completed).
  3. **Corequisites**: same as prerequisites, but the *same or an earlier* term.
  4. **Offered**: `take[c,t] = 0` if the course isn't offered in that season or
     year parity.
  5. **Load**: hours per term ≤ max (default 17 from the Bulletin; the student
     can lower it).
  6. **Requirements**: each required course is taken. Each "choose / at least
     N hours" group is met. Exclusions and mutual-exclusion pairs are respected.
  7. **Core placeholders** ("Division I slot", "Foreign language 200-level",
     ...) are 3-hour items, so the 120-hour total and the term loads are
     realistic.
  8. Timing rules from the Bulletin: FYS/WRI in year 1, HES 100 by the end of
     year 2, CSC 399 in the senior year.
- Objective: graduate by the target term (hard), then maximize the career
  relevance of the chosen electives, then prefer earlier placement of
  required courses.
- **Alternatives:** after finding plan #1, add a "different elective set"
  constraint and solve again. This gives 2-3 plans and a diff of how they
  differ.
- **"No valid plan" explanations:**
  (a) A cheap pre-check, in plain Python, walks each prerequisite chain.
      Example: "CSC 341 needs 250 → 251 → 341 = 3 terms, but only 2 remain."
  (b) Each rule group gets an on/off switch (an "assumption" in CP-SAT
      terms). When the problem is infeasible, CP-SAT reports which switches
      conflict, and we turn those into sentences.

### Recommender (the "AI" part)
1. Build TF-IDF vectors over each eligible course's title + description (only
   from the DB).
2. Career query = free text + keywords from the chosen pick-list profile (for
   example, `machine-learning-engineer` adds "learning, neural, data,
   statistics, optimization"). The team writes these profiles and the paper
   discusses them.
3. Score = cosine similarity, scaled to 0-100. The explanation is a template:
   "Matches your goal through *neural networks, learning*: '…quoted phrase
   from the Bulletin…'".
4. Optional: if `ANTHROPIC_API_KEY` (or similar) is set, the LLM rewrites
   that one sentence using *only* the quoted Bulletin text. The output is
   checked so that any course code it mentions exists in the DB; otherwise we
   fall back to the template.
5. Course IDs only ever come from the DB, so the recommender cannot invent
   courses.

---

## 3. Database schema (SQLite)

```sql
bulletin(year TEXT PK, fetched_at TEXT, source_url TEXT)

course(id TEXT PK,                 -- 'CSC 201'
       bulletin_year TEXT, subject TEXT, number TEXT, title TEXT,
       credits_min REAL, credits_max REAL, description TEXT,
       tags TEXT,                  -- JSON list: ["D","QDA"]
       repeatable INTEGER, override_allowed INTEGER,
       prereq_raw TEXT, prereq_parse_status TEXT,  -- ok | ambiguous | failed | none
       source_url TEXT, needs_review TEXT)         -- reason, or NULL

prerequisite(course_id, clause_no INTEGER, option_id TEXT, kind TEXT)
       -- CNF: all clauses required; any one option per clause satisfies it
       -- kind: course | level_atom ('CSC>=200') | manual
corequisite(course_id, clause_no, option_id, kind)
exclusive_pair(course_a, course_b)                 -- "credit not allowed for both"
course_program_exclusion(course_id, program_id, max_hours REAL NULL, note TEXT)

offering_pattern(course_id, term TEXT,             -- 'fall' | 'spring' | 'summer'
       year_parity TEXT,                           -- 'any' | 'odd' | 'even'
       source TEXT)                                -- 'bulletin' | 'override' | 'default-assumed'

program(id TEXT PK, bulletin_year, name, type,     -- 'major' | 'minor'
        degree TEXT, total_hours_note TEXT, source_url)
requirement_group(id PK, program_id, parent_id NULL, label, kind,
       -- all_of | choose_n_courses | min_hours_from_rule | free_text
       n REAL NULL, rule_json TEXT NULL,          -- {"subject":"CSC","min_level":300}
       sort_order INTEGER, needs_review TEXT)
requirement_option(group_id, option_no, course_id)  -- OR rows share option_no

core_category(id PK, bulletin_year, family,        -- basic | divisional | 21CS
       name, courses_required INTEGER, tag TEXT NULL, departments TEXT NULL,
       timing_rule TEXT NULL, rules_note TEXT)

term_calendar(term_id PK, bulletin_year, season, year,
       classes_begin DATE, classes_end DATE, exams_end DATE, is_estimated INTEGER)
```

Student profiles stay **out of the DB**. They are local YAML/JSON files with
fake data only.

---

## 4. Milestones (today is Mon 10/26; demo Wed 12/2)

| # | Milestone | Target | Done when |
|---|---|---|---|
| 0 | Repo setup: Python 3.12 venv, requirements, pytest, README | 10/27 | `pytest` runs on all teammates' laptops |
| 1 | Scraper + cache + validation report | 10/30 | `scripts/scrape.py` fetches about 10 pages once; `validate_data.py` lists parse problems |
| 2 | DB schema + prerequisite parser + program/core parser | 11/4 | All CSC/MTH prerequisites parse or are flagged; unit tests pass |
| 3 | Planner (CP-SAT) + plan validator, hard-coded fake student | 11/10 | A valid 8-semester B.S. plan; edge-case tests pass |
| 4 | Degree audit | 11/12 | Checklist matches a hand-checked sample |
| 5 | Career recommender | 11/16 | Top-5 for 3 sample careers look sensible, with scores and reasons |
| 6 | Streamlit UI | 11/20 | Full flow in the browser |
| 7 | Update / re-plan + diff | 11/23 | Mark a course failed → new plan + diff |
| — | Thanksgiving week buffer (11/25-29) | | |
| 8 | Export (CSV/.ics) + polish + demo script | 12/1 | Under-5-minute demo rehearsed twice |

Feature freeze: 11/24. After that, only bug fixes and the paper.

### Cut list (drop in this order if behind)
1. LLM-written explanations (the template explanations remain)
2. `.ics` export (keep CSV)
3. Infeasibility explanation from solver "assumptions" (keep the simple
   prerequisite-chain pre-check)
4. Top 2-3 alternatives → a single plan
5. Summer terms
6. B.A. and minor (keep the B.S. only)
7. Free-text career input → pick-list only

**Never cut:** valid plan, audit, re-plan with diff, disclaimer, fake-data-only.
