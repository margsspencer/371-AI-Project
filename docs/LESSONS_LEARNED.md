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
