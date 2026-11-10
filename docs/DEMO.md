# Demo script (under 5 minutes)

WFU Graduation Calendar Planner, CSC 371. All students in the demo are fictional.
Times are targets; rehearse twice with a timer.

## Before the demo (10 minutes ahead)

1. `source .venv/bin/activate`, then `pytest -q`. Everything should pass.
2. Start the app: `streamlit run src/gradplanner/ui/app.py`. Load Sam, press
   **Make my plan** once, then refresh the page. The first plan of a session is the
   slowest; this warms the caches.
3. Import a calendar file ahead of time: run
   `python scripts/export.py samples/casey_double_major.yaml` and import
   `exports/casey_example_fictional.ics` into a calendar app (a spare/test calendar),
   so you can switch to it in step 6 without waiting.
4. Browser zoom about 90% so a semester card and its table fit on screen. Close other tabs.
5. Backup: if the app fails, every step has a terminal command (listed under each step).

Each plan takes up to about 10 seconds to solve. Use that time to talk (the lines in
*italics* are written to fill it).

## 0:00 – 0:20 · The problem

> Planning four years at Wake means juggling prerequisites, which semesters courses
> run, major and minor rules, the core curriculum, hour limits, and study abroad.
> Our planner reads all of that from the 2026-27 Bulletin and builds a plan that
> provably follows the rules. It's advisory only: the box at the top says so.

Point at the **Advisory only** box.

## 0:20 – 1:20 · A plan for a first-year student

Sidebar: **Start from → Sam Example (fictional) → Load**. Mention: Computer Science
B.S., nothing completed, career goal "machine learning engineer, AI for healthcare".
Press **Make my plan**.

> *While it solves: the planner is a constraint solver (Google OR-Tools). Each rule
> is a constraint: a prerequisite must come in an earlier semester, at most 17 hours,
> every major requirement and core requirement covered. The solver searches for the
> best plan that breaks none of them.*

When it appears:
- Point at **Plan check: Valid**. *"A second, separate program re-checks every rule
  with plain loops. Two independent methods agree."*
- Scroll the semester cards; point at a ⭐ course: *"chosen because it matches the
  career goal."*

Backup: `python scripts/plan.py samples/sam_first_year_bs.yaml`

## 1:20 – 1:50 · Why those electives (the AI part)

Open the **Career picks** tab.

> Course descriptions from the Bulletin are turned into TF-IDF vectors and compared
> with the career's keywords. Each pick shows its score, the words that matched, and
> a quote from the Bulletin, so the reason is checkable. Course ids only come from
> the database, so it can't invent a course.

Backup: `python scripts/recommend.py --career machine-learning-engineer`

## 1:50 – 2:20 · Degree audit

Open the **Degree audit** tab. Point at the five counts (done / in progress /
planned / remaining / advisor review) and one section.

> This is the checklist an advisor would use. Anything we couldn't read reliably
> from the Bulletin shows as "advisor review" instead of being guessed.

## 2:20 – 3:05 · Something goes wrong: re-plan

Back to **Plan**. Open **Finished Fall 2026? Record how it went and re-plan**. Set
the first course in the list to **failed** (a CSC or MTH course), press **Record Fall
2026 and re-plan**.

> *While it solves: passed courses become completed, the failed one has to be
> retaken, and the solver is rewarded for keeping everything else where it was.*

Point at the **What changed** box: the 🔁 retake line and the short list of moves.
*"Without that stability reward, one failed course reshuffled 22 things; with it, only
the courses that depend on it move."* Point at the sidebar: the profile now starts
in Spring 2027.

Backup: `python scripts/replan.py samples/sam_first_year_bs.yaml --failed "<a Fall 2026 course>"`
(the script lists the Fall 2026 courses if you name one that isn't planned)

## 3:05 – 3:50 · Beyond computer science

Sidebar: **Casey Example (fictional) → Load → Make my plan**. CS + Math double major,
Statistics minor, junior spring in London.

> *While it solves: we cover 122 College and School of Business majors and minors.
> A course can count toward several programs, and pairs the Bulletin forbids, like
> Biology with Neuroscience, are refused.*

Point at the 🌍 **Spring 2028 · Study abroad** card: *"abroad semesters are planned
toward divisionals and up to 6 hours of the major, within each department's limits."*

Optional if time allows: Riley (Finance) shows no business courses before sophomore
spring, when students are admitted to the School of Business.

## 3:50 – 4:20 · Take it with you

Scroll to **Save your plan**. Click **📅 Calendar (.ics)**, then switch to the
calendar you imported beforehand: one event per semester listing its courses, plus
add/drop/withdraw deadlines and finals.

> Dates come from the Bulletin's academic calendar. Later years are projected and
> marked "estimated", since the Bulletin only publishes one year.

## 4:20 – 4:50 · How we know it works, and its limits

- More than 150 automated tests; every one of the 122 programs gets a plan that passes the
  independent check (`data/coverage_report.md`).
- 53 programs are read completely; 63 partly (the rest goes to "advisor review");
  6 are written as paragraphs, so only the core curriculum is planned.
- Limits: one Bulletin year for everyone; no course-section times or instructors; we
  assume courses run fall and spring unless the Bulletin says otherwise.

## 4:50 · Close

> It turns the Bulletin into a plan you can check, change, and take with you, and it
> tells you what to confirm with your advisor.

## Likely questions

- **Is the plan optimal?** "Best plan found" means the solver proved it; "good plan"
  means the 10-second limit ran out first. Either way it follows every rule.
- **What if it doesn't fit in four years?** The "no plan" message names the
  requirements that conflict. Students can allow summer courses or overloads (above
  17 hours needs permission); the planner overloads only when it has to.
- **Why not use professor ratings?** No allowed data source (RateMyProfessors forbids
  scraping; course evaluations aren't public), and instructors are only known one
  semester ahead.
- **What if the Bulletin changes?** Re-scrape (`scripts/scrape.py --all`, polite: 2 s
  between requests, robots.txt respected) and rebuild the database.
- **Real student data?** None. Every profile is fictional, and profiles never go into
  the database.
