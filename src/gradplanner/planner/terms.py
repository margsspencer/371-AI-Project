"""Semesters ("Fall 2026", "Spring 2027", "Summer 2027") and how they follow each other."""

from __future__ import annotations

from dataclasses import dataclass

# Order of seasons inside one calendar year.
_SEASON_ORDER = {"spring": 0, "summer": 1, "fall": 2}


@dataclass(frozen=True)
class Term:
    season: str   # fall | spring | summer
    year: int     # calendar year the term happens in (Spring 2027 -> 2027)

    @classmethod
    def parse(cls, text: str) -> Term:
        """'Fall 2026' -> Term('fall', 2026)."""
        try:
            season, year = text.split()
            season = season.lower()
            if season not in _SEASON_ORDER:
                raise ValueError
            return cls(season, int(year))
        except ValueError:
            raise ValueError(f"term must look like 'Fall 2026', got {text!r}") from None

    def __str__(self) -> str:
        return f"{self.season.capitalize()} {self.year}"

    @property
    def sort_key(self) -> tuple[int, int]:
        return (self.year, _SEASON_ORDER[self.season])

    def __lt__(self, other: Term) -> bool:
        return self.sort_key < other.sort_key

    def __le__(self, other: Term) -> bool:
        return self.sort_key <= other.sort_key

    @property
    def academic_year(self) -> int:
        """The fall year of the school year this term belongs to (Spring 2027 -> 2026; summer counts with spring)."""
        return self.year if self.season == "fall" else self.year - 1

    def next(self, include_summer: bool = False) -> Term:
        if self.season == "fall":
            return Term("spring", self.year + 1)
        if self.season == "spring":
            return Term("summer", self.year) if include_summer else Term("fall", self.year)
        return Term("fall", self.year)


def terms_between(first: Term, last: Term, include_summer: bool = False) -> list[Term]:
    """All terms from `first` to `last`, inclusive."""
    if last < first:
        return []
    out = [first]
    while out[-1] < last:
        out.append(out[-1].next(include_summer))
    return out
