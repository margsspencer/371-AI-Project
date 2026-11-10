-- Graduation planner database: one file per Bulletin year (data/bulletin_<year>.db).
-- Every table carries bulletin_year (directly or through its parent) so next year's data can sit alongside.

CREATE TABLE bulletin (
    year          TEXT PRIMARY KEY,
    built_at      TEXT NOT NULL,
    base_url      TEXT NOT NULL
);

CREATE TABLE course (
    id                TEXT PRIMARY KEY,         -- 'CSC 201'
    bulletin_year     TEXT NOT NULL REFERENCES bulletin(year),
    subject           TEXT NOT NULL,
    number            TEXT NOT NULL,
    title             TEXT NOT NULL,
    credits_min       REAL,
    credits_max       REAL,
    description       TEXT NOT NULL,
    tags              TEXT NOT NULL,            -- JSON list, e.g. ["D","QR"]
    prereq_raw        TEXT,
    prereq_status     TEXT NOT NULL,            -- ok | resolved_by_rule | manual | failed | none | override
    coreq_raw         TEXT,
    override_allowed  INTEGER NOT NULL,
    override_required INTEGER NOT NULL,
    min_standing      TEXT,
    notes             TEXT NOT NULL,            -- JSON list of review notes
    source_key        TEXT NOT NULL
);

-- Prerequisites in CNF: a course needs EVERY clause; a clause is met by ANY one of its options.
-- kind: course (value 'CSC 201') | level (value 'CSC:200') | manual (value = free text)
CREATE TABLE prerequisite (
    course_id  TEXT NOT NULL REFERENCES course(id),
    clause_no  INTEGER NOT NULL,
    option_no  INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    value      TEXT NOT NULL,
    PRIMARY KEY (course_id, clause_no, option_no)
);

CREATE TABLE corequisite (
    course_id  TEXT NOT NULL REFERENCES course(id),
    clause_no  INTEGER NOT NULL,
    option_no  INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    value      TEXT NOT NULL,
    PRIMARY KEY (course_id, clause_no, option_no)
);

CREATE TABLE offering_pattern (
    course_id    TEXT NOT NULL REFERENCES course(id),
    term         TEXT NOT NULL,                 -- fall | spring | summer
    year_parity  TEXT NOT NULL,                 -- any | odd | even
    source       TEXT NOT NULL,                 -- bulletin | override | default
    PRIMARY KEY (course_id, term, year_parity)
);

CREATE TABLE exclusive_pair (                   -- "Credit not allowed for both A and B"
    course_a  TEXT NOT NULL,
    course_b  TEXT NOT NULL,
    PRIMARY KEY (course_a, course_b)
);

CREATE TABLE program (
    id             TEXT PRIMARY KEY,            -- 'CSC-BS'
    bulletin_year  TEXT NOT NULL REFERENCES bulletin(year),
    name           TEXT NOT NULL,
    type           TEXT NOT NULL,               -- major | minor
    degree         TEXT,                        -- BS | BA | NULL
    subject        TEXT NOT NULL,
    intro          TEXT NOT NULL,
    source_key     TEXT NOT NULL,
    school         TEXT NOT NULL DEFAULT 'College'   -- College | Business
);

CREATE TABLE requirement_group (
    id          INTEGER PRIMARY KEY,
    program_id  TEXT NOT NULL REFERENCES program(id),
    label       TEXT NOT NULL,
    sort_order  INTEGER NOT NULL
);

CREATE TABLE requirement_item (
    id            INTEGER PRIMARY KEY,
    group_id      INTEGER NOT NULL REFERENCES requirement_group(id),
    kind          TEXT NOT NULL,                -- course | choose | hours | free_text
    label         TEXT NOT NULL,
    n             INTEGER NOT NULL,
    hours         REAL,
    subject       TEXT,
    min_level     INTEGER,
    needs_review  TEXT,
    sort_order    INTEGER NOT NULL
);

CREATE TABLE requirement_option (
    item_id    INTEGER NOT NULL REFERENCES requirement_item(id),
    option_no  INTEGER NOT NULL,
    course_id  TEXT,                            -- NULL when the option is free text
    free_text  TEXT,
    PRIMARY KEY (item_id, option_no)
);

CREATE TABLE course_program_exclusion (
    course_id   TEXT NOT NULL,
    program_id  TEXT NOT NULL REFERENCES program(id),
    max_hours   REAL,                           -- NULL = may not count at all
    note        TEXT NOT NULL
);

CREATE TABLE core_category (
    id                TEXT PRIMARY KEY,         -- 'DIV-I', '21CS-QDA', 'QR'
    bulletin_year     TEXT NOT NULL REFERENCES bulletin(year),
    family            TEXT NOT NULL,            -- basic | divisional | 21CS | divisional-wide
    name              TEXT NOT NULL,
    courses_required  INTEGER NOT NULL,
    tag               TEXT,
    departments       TEXT,
    course_cnf        TEXT NOT NULL,            -- JSON (same encoding as prerequisite rows)
    timing            TEXT,
    placeholder       INTEGER NOT NULL,
    rules_note        TEXT,
    source            TEXT NOT NULL,            -- 'bulletin' or who decided it
    needs_review      TEXT
);

CREATE TABLE degree_rule (
    key          TEXT PRIMARY KEY,              -- 'total_hours', 'max_hours_per_semester', ...
    value        REAL NOT NULL,
    source_key   TEXT NOT NULL,
    source_text  TEXT NOT NULL                  -- the Bulletin sentence it came from
);

-- Academic calendar rows (milestone 8): one per Bulletin calendar line.
-- Later years are projected when needed (parser/calendar.py), not stored.
CREATE TABLE calendar_event (
    season      TEXT NOT NULL,                 -- fall | spring
    year        INTEGER NOT NULL,              -- calendar year of the term
    start_date  TEXT NOT NULL,                 -- ISO date
    end_date    TEXT NOT NULL,
    name        TEXT NOT NULL                  -- 'Classes begin', 'Fall final exams', ...
);

CREATE TABLE subject (
    code  TEXT PRIMARY KEY,      -- 'CSC'
    name  TEXT NOT NULL          -- 'Computer Science' (from Courses A-Z)
);
