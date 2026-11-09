# Graduation Calendar Planner (WFU): CSC 371 AI Project

Plans a semester-by-semester path to graduation for Wake Forest students in
any College major or minor or School of Business program (122 programs; see
"Coverage" below), using the official
[2026-2027 Undergraduate Bulletin](https://bulletin.wfu.edu/undergraduate/)
as its only source of rules and courses.

> **Disclaimer:** recommendations are advisory. Students must confirm every
> plan with their academic advisor and the current Bulletin.

Design: [docs/DESIGN.md](docs/DESIGN.md). Lessons learned: [docs/LESSONS_LEARNED.md](docs/LESSONS_LEARNED.md).

## Setup (fresh laptop)

You need **Python 3.11 or newer**. Check with `python3 --version`.
If it's older, either:
- install Python 3.12 from https://www.python.org/downloads/ (easiest), or
- use `uv`, which needs no admin rights: `pip3 install --user uv`, then `uv python install 3.12`.

Then, from the project folder:

```bash
python3.12 -m venv .venv          # or: <path printed by `uv python find 3.12`> -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .                  # makes the `gradplanner` package importable
pytest -q                         # all tests should pass
```

## Build the data

```bash
python scripts/scrape.py --all    # finds and downloads ~330 Bulletin pages once (2 s apart, ~11 min), cached in data/raw/
python scripts/build_db.py        # parses everything -> data/bulletin_2026-2027.db + data/validation_report.md
python scripts/show.py "CSC 374"  # look up a course, a program (CSC-BS, BIO-BS, FIN-BS), or "core"
python scripts/coverage.py        # tries a plan in every program -> data/coverage_report.md (~20 min)
```

Without `--all`, only the CS pages are downloaded and only the CS programs are built.

### Coverage

Each program's requirement table is read automatically, and each gets a level:
- **full**: every requirement row was understood and is planned.
- **partial**: some rows are written in a way the parser can't check (e.g.
  "Option 1 / Option 2", portfolios, auditions). Those show as "advisor review"
  in the audit; everything else is planned.
- **none**: the page describes requirements in paragraphs (e.g. the History
  minor); only the core curriculum is planned.

`data/coverage_report.md` lists every program with its level and whether a
plan was found and passed the independent check. Certificates, concentrations,
and self-designed majors are out of scope.

`scripts/validate_data.py` writes only the report, without rebuilding the database.

## Web app

```bash
streamlit run src/gradplanner/ui/app.py
```

Opens in the browser. Fill in a fictional student in the sidebar (or load one of the
samples), press **Make my plan**, and see the semester plan, career picks, and degree
audit in tabs. Programs whose requirements were only partly read show a banner, and the
rows that need an advisor appear as "advisor review" in the audit.

Students can pick up to two majors and two minors. A course may count toward each
program it fits (several departments say so; the Bulletin sets no general limit).
Major pairs the Bulletin forbids, such as Biology with Neuroscience, are refused
(list in `data/overrides/team_decisions.yaml`). School of Business courses are
planned from sophomore spring on for business majors (after admission), except
the admission prerequisites; non-business students only get them when one of
their programs requires them, since others need instructor permission.

**Study abroad.** Tick "I plan to study abroad" and pick one or two semesters (junior
spring by default) and the program type. An abroad semester holds "abroad course"
slots instead of catalog courses: divisionals, the language requirement, 21st-century
core (Wake Forest programs only), and major/minor elective credit within each
program's limit. Limits: business majors 6 hours; Sociology 6; Entrepreneurship
minor 6; Spanish 3 from affiliate programs; other College programs 6 (a team
assumption: the Bulletin states no limit). Affiliate (non-Wake Forest) programs are
transfer credit: they count toward the 30-hour outside-credit limit and can't be in
the senior year. All of this is in `data/overrides/team_decisions.yaml`.

## Make a plan

```bash
python scripts/plan.py samples/sam_first_year_bs.yaml     # first-year B.S. student, 8 semesters
python scripts/plan.py samples/jordan_junior_bs.yaml      # rising junior with AP + core credit
python scripts/plan.py samples/riley_first_year_finance.yaml   # Finance major + Economics minor
python scripts/plan.py samples/casey_double_major.yaml    # CS + Math double major, Stats minor, London
```

Prints each semester (courses, core "choose a course" slots, free electives,
and what each counts toward), notes to check with an advisor, and the result
of the independent plan check. If no plan fits, it says why. Profiles are
YAML files with **fake data only**; copy a sample to make a new one.

## Re-plan after a semester

```bash
python scripts/replan.py samples/sam_first_year_bs.yaml --failed "CSC 111"
python scripts/replan.py samples/jordan_junior_bs.yaml --dropped "CSC 250" --save after.yaml
```

Marks the first planned semester (everything passed unless listed as failed or
dropped), plans again from the next semester, and lists what changed: done, failed and
planned again, moved, added, removed. The new plan keeps the old one's choices where it
can, so a failed course moves only the courses that depend on it. In the web app, open
**Finished <semester>? Record how it went and re-plan** under the plan; the sidebar then
shows the updated profile. "Make my plan" after editing the profile also compares with
the previous plan (untick "Keep my previous plan where possible" for a fresh one).


```bash
python scripts/recommend.py --list                           # career pick-list
python scripts/recommend.py --career machine-learning-engineer
python scripts/recommend.py --text "robotics and computer vision"
```

Ranks courses the student could still take by how well their Bulletin
description matches the career (TF-IDF word matching, score 0-100), with the
matching words and a quoted sentence as the reason. Add `career:` and/or
`career_text:` to a profile and `scripts/plan.py` will prefer those courses
when it picks electives. Career keyword lists are in
`src/gradplanner/recommender/careers.yaml`.

## Degree audit

```bash
python scripts/audit.py samples/jordan_junior_bs.yaml          # what's done and what's left
python scripts/audit.py samples/jordan_junior_bs.yaml --plan   # also count a generated plan
```

One line per requirement (major, co-requirements, core, 120 hours), marked
done, in progress, planned, remaining, or advisor review, with the courses
used. Courses the student is taking now go under `in_progress:` in the
profile; the planner assumes they are passed.

### Fixing data by hand (`data/overrides/`)
| File | What it does |
|---|---|
| `team_decisions.yaml` | Rules the team decided that are not on the Bulletin pages (QR requirement, default offering pattern, and/or rule, core slot hours, subject name aliases, subject-to-division names), each with its source |
| `prereq_overrides.yaml` | Replaces a course's parsed prerequisite. Same syntax as the Bulletin; `[brackets]` = a condition a human must check |
| `offerings.csv` | When a course is offered. `terms` is `fall`, `spring`, `summer`, separated by `;`. `year_parity` is `any`, `odd`, or `even` |

After editing any of these, run `python scripts/build_db.py` again.

The scraper follows `robots.txt`, waits 2 seconds between requests, and
reuses cached pages. Use `--refresh` only when the Bulletin changes.

## Project layout

```
src/gradplanner/
  config.py       Bulletin year, paths, scraper settings
  scraper/        polite fetcher + list of Bulletin pages
  parser/         HTML -> courses, prerequisite logic (prereq.py), programs, core rules
  models.py       Catalog / Course / Program: the data model everything else uses
  pipeline.py     cached pages + overrides -> Catalog
  db/             SQLite schema + save/load
  validation.py   checks that flag data needing a human fix
  planner/        model.py (CP-SAT solver), validate_plan.py (independent check),
                  precheck.py ("no plan" reasons), student.py (profiles), terms.py
  audit/          audit.py: requirement checklist (works with or without a plan)
  recommender/    tfidf.py (career matching), careers.yaml (pick-list keywords)
scripts/          command-line entry points
samples/          fictional student profiles (YAML)
tests/            pytest (synthetic fixtures only, no real student data)
data/overrides/   hand-made fixes to Bulletin data (committed)
```

## Privacy

No real student data is stored or sent anywhere. Sample students are fictional.
