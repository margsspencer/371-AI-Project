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
