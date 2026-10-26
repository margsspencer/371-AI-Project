# Graduation Calendar Planner (WFU CS): CSC 371 AI Project

Plans a semester-by-semester path to graduation for Wake Forest Computer
Science students (B.S., B.A., minor), using the official
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
python scripts/scrape.py          # downloads ~10 Bulletin pages once (2 s apart), caches in data/raw/
python scripts/build_db.py        # parses everything -> data/bulletin_2026-2027.db + data/validation_report.md
python scripts/show.py "CSC 374"  # look up a course, a program (CSC-BS), or "core"
```

`scripts/validate_data.py` writes only the report, without rebuilding the database.

### Fixing data by hand (`data/overrides/`)
| File | What it does |
|---|---|
| `team_decisions.yaml` | Rules the team decided that are not on the Bulletin pages (QR requirement, default offering pattern, and/or rule), each with its source |
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
scripts/          command-line entry points
tests/            pytest (synthetic fixtures only, no real student data)
data/overrides/   hand-made fixes to Bulletin data (committed)
```

## Privacy

No real student data is stored or sent anywhere. Sample students are fictional.
