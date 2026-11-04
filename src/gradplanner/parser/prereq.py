"""Turn prerequisite sentences into AND/OR logic.

Input  (from the Bulletin):  "CSC 201 and 270, MTH 112 and MTH 121 or 205"
Output (CNF, an "AND of ORs"):
    [[CSC 201], [CSC 270], [MTH 112], [MTH 121, MTH 205]]
which reads: CSC 201 AND CSC 270 AND MTH 112 AND (MTH 121 OR MTH 205).

Why CNF? Every clause is a simple "at least one of these" rule. The
planner can turn each clause into one constraint, and checking whether a
student meets a prerequisite is just "every clause has a completed option".

How it works, in three steps:
1. Tokenize: split the text into course codes, "and", "or", commas, parentheses.
   A bare number like "270" gets the subject of the last code we saw ("CSC").
   Special phrases ("any 200-level CSC course") become one LEVEL token.
2. Parse into a small tree. Team rule for mixed and/or without parentheses
   (decided 2026-10-26): "or" binds tighter than "and", so "A and B or C"
   means A and (B or C), i.e. both B and C are offered as options.
3. Convert the tree to CNF with the distributive law.

Anything the parser doesn't understand becomes a ManualAtom (a condition a
human must check, e.g. "senior STA major"). It is never silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product
from typing import Iterable, Union

# ---------------------------------------------------------------------------
# Atoms: the smallest things a prerequisite can require
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CourseAtom:
    """A specific course must be completed, e.g. CSC 201."""
    code: str

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True)
class LevelAtom:
    """Any course in `subject` numbered `min_level` or higher, e.g. any 200-level CSC course."""
    subject: str
    min_level: int

    def __str__(self) -> str:
        return f"any {self.subject} {self.min_level}+"


@dataclass(frozen=True)
class ManualAtom:
    """A condition the program cannot check (class standing, test scores...)."""
    text: str

    def __str__(self) -> str:
        return f"[manual: {self.text}]"


Atom = Union[CourseAtom, LevelAtom, ManualAtom]
CNF = list[list[Atom]]          # AND over clauses, OR inside each clause


@dataclass
class ParseResult:
    cnf: CNF
    status: str        # ok | resolved_by_rule | manual | failed | none
    notes: list[str]


# ---------------------------------------------------------------------------
# Step 1: tokenize
# ---------------------------------------------------------------------------

_LEVEL_PATTERNS = [
    # "any 200-level CSC course"
    re.compile(r"any (?P<lvl>\d{3})-level (?P<subj>[A-Z]{2,4}) course"),
    # "one course in STA at the 200 level or above"
    re.compile(r"one course in (?P<subj>[A-Z]{2,4}) at the (?P<lvl>\d{3})[- ]level or above"),
]
# Course codes are case-sensitive on purpose ("and 250" must NOT look like a code);
# only the connector words use (?i:...) to ignore case.
_TOKEN_RE = re.compile(
    r"(?P<code>\b[A-Z]{2,4} \d{3}[A-Z]?\b)"
    r"|(?P<num>\b\d{3}[A-Z]?\b)"
    r"|(?P<level>§LEVEL\d+§)"
    r"|(?P<manual>\[[^\]]+\])"            # [square brackets] = manual condition (overrides file)
    r"|(?P<oneof>\b(?i:one of the following)(?: (?i:courses))?:?)"
    r"|(?P<and>\b(?i:and)\b)"
    r"|(?P<or>\b(?i:or)\b)"
    r"|(?P<lp>\()|(?P<rp>\))"
    r"|(?P<sep>[,;])"
    r"|(?P<word>[^\s,;()]+)"
)
# Filler words that carry no logic: "either A or B", "one of A or B", "both".
_FILLER = {"either", "both", "one", "of"}


@dataclass
class Token:
    kind: str     # atom | and | or | lp | rp | sep | oneof
    value: object = None


def tokenize(text: str) -> list[Token]:
    """Split prerequisite text into tokens (see module docstring)."""
    levels: list[LevelAtom] = []

    def _stash_level(m: re.Match) -> str:
        levels.append(LevelAtom(m.group("subj").upper(), int(m.group("lvl"))))
        return f" §LEVEL{len(levels) - 1}§ "

    for pat in _LEVEL_PATTERNS:
        text = pat.sub(_stash_level, text)

    tokens: list[Token] = []
    last_subject: str | None = None
    words: list[str] = []          # unknown words collected into one ManualAtom

    def _flush_words() -> None:
        meaningful = [w for w in words if w.lower() not in _FILLER]
        if meaningful:
            tokens.append(Token("atom", ManualAtom(" ".join(words))))
        words.clear()

    for m in _TOKEN_RE.finditer(text):
        kind = m.lastgroup
        if kind == "word":
            words.append(m.group())
            continue
        _flush_words()
        if kind == "code":
            code = m.group().upper()
            last_subject = code.split()[0]
            tokens.append(Token("atom", CourseAtom(code)))
        elif kind == "num":
            if last_subject is None:
                tokens.append(Token("atom", ManualAtom(m.group())))
            else:
                tokens.append(Token("atom", CourseAtom(f"{last_subject} {m.group().upper()}")))
        elif kind == "manual":
            tokens.append(Token("atom", ManualAtom(m.group()[1:-1].strip())))
        elif kind == "level":
            idx = int(re.search(r"\d+", m.group()).group())
            tokens.append(Token("atom", levels[idx]))
        else:
            tokens.append(Token(kind.lower()))
    _flush_words()
    return tokens


# ---------------------------------------------------------------------------
# Step 2: parse into a tree  ("and"/"or" nodes holding atoms or sub-trees)
# ---------------------------------------------------------------------------
#   clauses := group ( SEP [and] group )*          -> AND
#   group   := orexpr ( and orexpr )*              -> AND
#   orexpr  := primary ( or primary )*             -> OR   ("or" binds tighter)
#   primary := atom | ( clauses ) | ONEOF list     (list = atoms joined by , / or / and -> OR)


class ParseError(Exception):
    pass


Tree = Union[Atom, tuple]   # ("and", [children]) or ("or", [children])


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos].kind if self.pos < len(self.tokens) else None

    def take(self, kind: str) -> Token:
        if self.peek() != kind:
            raise ParseError(f"expected {kind} at token {self.pos}, found {self.peek()}")
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self) -> Tree:
        tree = self.clauses()
        if self.pos != len(self.tokens):
            raise ParseError(f"unexpected {self.peek()} at token {self.pos}")
        return tree

    def clauses(self) -> Tree:
        parts = [self.group()]
        while self.peek() == "sep":
            self.take("sep")
            if self.peek() == "and":
                self.take("and")
            if self.peek() in (None, "rp"):      # trailing comma
                break
            parts.append(self.group())
        return parts[0] if len(parts) == 1 else ("and", parts)

    def group(self) -> Tree:
        parts = [self.orexpr()]
        while self.peek() == "and":
            self.take("and")
            parts.append(self.orexpr())
        return parts[0] if len(parts) == 1 else ("and", parts)

    def orexpr(self) -> Tree:
        parts = [self.primary()]
        while self.peek() == "or":
            self.take("or")
            parts.append(self.primary())
        return parts[0] if len(parts) == 1 else ("or", parts)

    def primary(self) -> Tree:
        kind = self.peek()
        if kind == "atom":
            return self.take("atom").value
        if kind == "lp":
            self.take("lp")
            inner = self.clauses()
            self.take("rp")
            return inner
        if kind == "oneof":
            # "one of the following courses: A, B, C" -> A or B or C (to the end of the list)
            self.take("oneof")
            options = [self.take("atom").value]
            while self.peek() in ("sep", "or", "and") and self.pos + 1 < len(self.tokens) \
                    and self.tokens[self.pos + 1].kind in ("atom", "or", "and"):
                self.pos += 1
                if self.peek() in ("or", "and"):
                    self.pos += 1
                options.append(self.take("atom").value)
            return ("or", options)
        raise ParseError(f"unexpected {kind} at token {self.pos}")


# ---------------------------------------------------------------------------
# Step 3: tree -> CNF
# ---------------------------------------------------------------------------


def to_cnf(tree: Tree) -> CNF:
    """Convert a tree to a list of OR-clauses that must all hold."""
    if not isinstance(tree, tuple):
        return [[tree]]
    op, children = tree
    child_cnfs = [to_cnf(c) for c in children]
    if op == "and":
        return [clause for cnf in child_cnfs for clause in cnf]
    # op == "or": (A1 and A2) or (B1) = (A1 or B1) and (A2 or B1)  -- distributive law
    result: CNF = []
    for combo in product(*child_cnfs):
        merged: list[Atom] = []
        for clause in combo:
            for atom in clause:
                if atom not in merged:
                    merged.append(atom)
        result.append(merged)
    return result


def _dedupe(cnf: CNF) -> CNF:
    seen: list[list[Atom]] = []
    for clause in cnf:
        if clause not in seen:
            seen.append(clause)
    return seen


def parse_prereq(text: str | None) -> ParseResult:
    """Parse one prerequisite (or corequisite) string. Never raises."""
    if text is None or not text.strip():
        return ParseResult([], "none", [])
    notes: list[str] = []
    cleaned = text.strip().rstrip(".")
    try:
        tree = _Parser(tokenize(cleaned)).parse()
    except ParseError as exc:
        return ParseResult([[ManualAtom(cleaned)]], "failed", [f"could not parse: {exc}"])

    cnf = _dedupe(to_cnf(tree))
    status = "ok"
    if re.search(r"\band\b", cleaned) and re.search(r"\bor\b", cleaned) and "(" not in cleaned:
        status = "resolved_by_rule"
        notes.append("mixed and/or without parentheses: read as 'A and (B or C)' (team rule 2026-10-26)")
    if any(isinstance(a, ManualAtom) for clause in cnf for a in clause):
        status = "manual"
        notes.append("contains a condition the planner cannot check automatically")
    return ParseResult(cnf, status, notes)


# ---------------------------------------------------------------------------
# Checking a prerequisite against what a student has done
# ---------------------------------------------------------------------------


def _course_level(code: str) -> tuple[str, int]:
    subject, number = code.split()
    return subject, int(re.match(r"\d+", number).group())


def atom_satisfied(atom: Atom, completed: Iterable[str]) -> bool:
    """True if `atom` is met by the set of completed course codes. Manual atoms are never auto-met."""
    completed = set(completed)
    if isinstance(atom, CourseAtom):
        return atom.code in completed
    if isinstance(atom, LevelAtom):
        return any(_course_level(c)[0] == atom.subject and _course_level(c)[1] >= atom.min_level
                   for c in completed)
    return False


def cnf_satisfied(cnf: CNF, completed: Iterable[str]) -> bool:
    """True if every clause has at least one satisfied option."""
    completed = set(completed)
    return all(any(atom_satisfied(a, completed) for a in clause) for clause in cnf)


def unmet_clauses(cnf: CNF, completed: Iterable[str]) -> CNF:
    """Return the clauses that are not yet satisfied (useful for explanations)."""
    completed = set(completed)
    return [clause for clause in cnf if not any(atom_satisfied(a, completed) for a in clause)]


def cnf_to_text(cnf: CNF) -> str:
    """Human-readable form: 'CSC 201 AND (MTH 121 OR MTH 205)'."""
    parts = []
    for clause in cnf:
        names = [str(a) for a in clause]
        parts.append(names[0] if len(names) == 1 else "(" + " OR ".join(names) + ")")
    return " AND ".join(parts)


# ---------------------------------------------------------------------------
# Cleaning Bulletin wording before parsing (all departments)
# ---------------------------------------------------------------------------
# Other departments write prerequisites in many styles. These rules rewrite
# the common ones into the form the parser above understands. Each rule that
# fires adds a note, so the validation report shows what was changed.


@dataclass
class CleanedPrereq:
    text: str                  # what to parse as the prerequisite
    coreq: str                 # corequisite text found inside the prerequisite ("P or C- X", "C- X")
    standing: str | None       # "sophomore" | "junior" | "senior" from "junior or senior standing"
    notes: list[str]


_CODE = r"[A-Z]{2,4} \d{3}[A-Z]?"
_NUM = r"(?:[A-Z]{2,4} )?\d{3}[A-Z]?"
_NOTE_PAREN = re.compile(r"recommend|not open|offered|if taken as|required|only|majors|equivalent credit|"
                         r"camera|depending|concurrent", re.I)
_DROP_PHRASES = [r"depending on the topic", r"in the same dialect", r"with associated labs",
                 r";?\s*knowledge of [^;]*recommended", r"\(all majors\)"]


def clean_prereq_text(text: str | None, subject: str | None = None,
                      known_codes: set[str] | None = None) -> CleanedPrereq:
    """Rewrite common Bulletin prerequisite styles. `subject` is the course's own subject
    (used for "200-level course" with no subject named). `known_codes` (all catalog course
    ids) lets ranges like "SPA 310 - SPA 319" expand to the courses that exist."""
    if not text:
        return CleanedPrereq(text or "", "", None, [])
    s, notes, coreq, standing = text.strip(), [], "", None

    def note(msg: str) -> None:
        if msg not in notes:
            notes.append(msg)

    s = re.sub(r"^[\s\-–:]+", "", s)                                # "- FRH 216" (stray dash)
    if re.search(r"\bmajors? only\b", s, re.I) and re.search(r"\bC\s?[-\u2013]", s):
        s = re.sub(r"[;,]?\s*(?:or )?(?:P or )?C\s?[-\u2013].*$", "", s).strip()
        note("corequisite that applies only to some majors dropped")
    s = re.sub(r"^or\s+", "", s)
    s = re.sub(r"\bP or C\b(?!\s*[-\u2013])", "P or C-", s)              # "P or C MTH 111" (dash missing)
    s = re.sub(r"\b([A-Z]{2,4})(\d{3}[A-Z]?)\b", r"\1 \2", s)          # "STA111" -> "STA 111"
    s = re.sub(r"\bAND\b", "and", s)

    # Class standing -> a separate field
    m = re.search(r"\b(sophomore|junior|senior)(?: or (?:junior|senior))?(?: major)? (?:standing|status)\b"
                  r"|^\s*(senior) majors\s*$", s, re.I)
    if m:
        standing = (m.group(1) or m.group(2)).lower()
        s = s[:m.start()] + s[m.end():]
        note(f"class standing '{m.group()}' stored separately")

    # "any two of the following ...: A (or A2), B or C" -> at least two of the options
    m = re.match(r"^\s*(?:any )?(two|three) of the following[^:]*:\s*(.+)$", s, re.I)
    if m:
        k = {"two": 2, "three": 3}[m.group(1).lower()]
        body = re.sub(r"\s*\(\s*or\s+([^()]+)\)", r" / \1", m.group(2).rstrip(". "))   # "A (or A2)" -> "A / A2"
        parts = [p.strip() for p in re.split(r",\s*|\s+or\s+", body) if p.strip()]
        exprs = [f"({p.replace(' / ', ' or ')})" if " / " in p else p for p in parts]
        if len(exprs) > k:
            from itertools import combinations
            # at least k of n  <=>  every group of (n - k + 1) options contains one taken
            groups = combinations(exprs, len(exprs) - k + 1)
            s = " and ".join("(" + " or ".join(g) + ")" for g in groups)
            note(f"'any {m.group(1)} of the following' written as and/or logic")

    # "X must be taken before or concurrently with this course" -> corequisite
    m = re.match(rf"^({_CODE}) must be taken before or concurrently with this course", s)
    if m:
        return CleanedPrereq("", m.group(1), standing, notes + ["'before or concurrently' read as a corequisite"])

    # "(or instead of MTH 205, P- MTH 121 and 251)" -> "(or MTH 121 and 251)"
    s = re.sub(rf"or instead of {_CODE},\s*P-\s*", "or ", s)

    # Grade conditions are not checked (we don't track grades)
    before = s
    for pat in (r"(?:with )?(?:a )?(?:grade of )?C-? or (?:better|higher)(?: in)?",
                r"(?:a )?minimum (?:grade )?of C-?(?: in)?"):
        s = re.sub(pat, "", s, flags=re.I)
    if s != before:
        note("grade requirement (e.g. 'C or better') not checked; the planner assumes you pass")

    # Parentheses that hold a note rather than logic
    def paren(m: re.Match) -> str:
        inner = m.group(1)
        if re.search(_CODE + "|" + r"\b\d{3}\b", inner) and not _NOTE_PAREN.search(inner):
            return m.group()
        note(f"note dropped from prerequisite: '({inner})'")
        return ""
    s = re.sub(r"\(([^()]*)\)", paren, s)
    for pat in _DROP_PHRASES:
        if re.search(pat, s, re.I):
            s = re.sub(pat, "", s, flags=re.I)
            note("descriptive phrase dropped from prerequisite")

    # Corequisites written inside the prerequisite: "...; P or C- X", "..., C- X"
    m = re.search(r"(?:^|[;,]\s*|\band\s+|\s)(?:P or C\s?[-–]|C or P\s?[-–]|C\s?[-–])\s*", s)
    if m:
        coreq = re.sub(r"[;,]?\s*(?:P or C\s?[-–]|C or P\s?[-–]|C\s?[-–])\s*", ", ", s[m.end():])
        s = s[:m.start()]
        note("corequisite written inside the prerequisite ('C-' / 'P or C-') moved to corequisites")

    # "or equivalent", "or placement", "or POI" -> a manual option
    s = re.sub(r"[,;]?\s*or (?:the )?(equivalent|placement|POI|permission of (?:the )?instructor|"
               r"prerequisite override)\b", r" or [\1]", s, flags=re.I)
    s = re.sub(r"\band/or\b", "or", s)
    s = re.sub(r";\s*or\b", " or", s)
    s = re.sub(r"\s+for [A-Z]{2,4} majors\b", "", s)

    # "an applied statistics course such as A, B, or C" -> the examples become the options
    if re.search(r"\ban? [^,;()]*? course,? such as ", s, re.I):
        s = re.sub(r"\ban? [^,;()]*? course,? such as ", "", s, flags=re.I)
        note("'a course such as A, B, or C': the listed examples are used as the options; ask about others")

    # Ranges: "SPA 310 - SPA 319", "PSY 220 -299" -> the catalog courses in that range
    def course_range(m: re.Match) -> str:
        subj, lo, hi = m.group(1), int(m.group(2)), int(m.group(3))
        codes = sorted(c for c in (known_codes or set())
                       if c.split()[0] == subj and lo <= int(re.match(r"\d+", c.split()[1]).group()) <= hi)
        note(f"range {subj} {lo}-{hi} read as any course in it")
        return "(" + " or ".join(codes) + ")" if codes else f"[any {subj} course numbered {lo}-{hi}]"
    s = re.sub(r"(?:any course in |at least \d+ credit hours from )?([A-Z]{2,4}) (\d{3})\s*[-\u2013]\s*"
               r"(?:[A-Z]{2,4} )?(\d{3})(?: sequence)?", course_range, s)

    # "X (or Y and Z)" -> "(X or (Y and Z))"
    s = re.sub(rf"({_NUM}) \(or ([^()]+)\)", r"(\1 or (\2))", s)
    s = re.sub(r"^\s*Prerequisite override required\b", "[prerequisite override]", s)

    # Slashes: "CHM 111 /111L" = lecture and lab together; "BIO/BMB/ CHM 370" and "A / B" = or
    s = re.sub(r"\b([A-Z]{2,4}) (\d{3})\s*/\s*\2L\b", r"(\1 \2 and \1 \2L)", s)
    s = re.sub(r"\b((?:[A-Z]{2,4}\s*/\s*)+)([A-Z]{2,4}) (\d{3}[A-Z]?)",
               lambda m: " or ".join(f"{x.strip()} {m.group(3)}" for x in m.group(1).split("/") if x.strip())
               + f" or {m.group(2)} {m.group(3)}", s)
    if "/" in s:
        s = re.sub(r"\s*/\s*", " or ", s)
        note("'/' read as 'or'")

    # "A, B, or C" (comma list ending in "or") -> A or B or C
    def or_list(m: re.Match) -> str:
        note("comma list ending in 'or' read as one choice")
        return re.sub(r",\s*", " or ", m.group(1)) + m.group(2)
    s = re.sub(rf"((?:{_NUM},\s*)+)or\s+({_NUM})", or_list, s)

    # Level phrases -> "any NNN-level SUBJ course"
    own = subject or ""
    level_rules = [
        (r"\b(?:any|a|one)?\s*(?:previous )?(\d{3})[- ]level ((?-i:[A-Z]{2,4})) courses?", r"any \1-level \2 course"),
        (r"\b((?-i:[A-Z]{2,4})) (\d{3})[- ]level courses?", r"any \2-level \1 course"),
        (r"\b(?:any|a|one)\s+(\d{3})[- ]level courses?", rf"any \1-level {own} course"),
        (r"^\s*(\d{3})[- ]level(?: courses?)?", rf"any \1-level {own} course"),
        (r"\b(?:any|one|a) ((?-i:[A-Z]{2,4})) courses?\b", r"any 100-level \1 course"),
        (r"\bone course in ((?-i:[A-Z]{2,4}))\b(?! at the)", r"any 100-level \1 course"),
    ]
    for pat, repl in level_rules:
        if re.search(pat, s, flags=re.I):
            s = re.sub(pat, repl, s, count=0, flags=re.I)
            note("level phrase read as 'any course at that level or above'")
    s = re.sub(r"\bany (\d{3})-level ([a-z]{2,4}) course", lambda m: f"any {m.group(1)}-level {m.group(2).upper()} course", s)
    s = re.sub(r"\bat least one of the following\b", "one of the following", s, flags=re.I)

    # A bare number at the start ("150L" after "a grade of C or higher in") is the course's own subject
    if subject and re.match(r"^\s*\d{3}[A-Z]?\b", s):
        s = f"{subject} " + s.strip()

    # Tidy leftover separators
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*([,;])(\s*[,;])+", r"\1", s)
    s = re.sub(r"(?:\b(?:and|or)\s*)+$|^\s*(?:and|or)\b", "", s.strip(" ,;")).strip(" ,;")
    s = re.sub(r"\band\s*([,;])", r"\1", s)
    coreq = coreq.strip(" ,;")
    return CleanedPrereq(s, coreq, standing, notes)
