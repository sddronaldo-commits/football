"""Joining players across sources.

Understat, football-data.org and the Transfermarkt dataset spell the same player
three different ways. Accents, mononyms, double surnames and initials all differ.
This is the single largest source of quiet data loss in a multi-league pipeline,
so the join is explicit, measurable and reviewable:

  1. normalise both sides (fold accents, drop punctuation, lowercase)
  2. exact match within the same league
  3. fuzzy match above a similarity floor, still within the same league
  4. anything left over is looked up in config/overrides.csv, which is hand
     maintained and version controlled

`match_rate` is asserted in the test suite. If a source changes its formatting
and the join silently degrades, CI fails instead of the site quietly losing a
third of its players.
"""

from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from .config import ROOT

OVERRIDES_PATH = ROOT / "config" / "overrides.csv"
FUZZY_FLOOR = 0.88


def normalise(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    kept = [ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in folded]
    return " ".join("".join(kept).split())


def load_overrides(path: Path | None = None) -> dict[tuple[str, str], str]:
    path = path or OVERRIDES_PATH
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return {
            (row["league"], normalise(row["source_name"])): normalise(row["canonical_name"])
            for row in csv.DictReader(handle)
            if row.get("league")
        }


@dataclass
class JoinReport:
    matched: int = 0
    total: int = 0
    unmatched: list[str] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        return self.matched / self.total if self.total else 1.0


def attach_market_values(players: list[dict], values: list[dict]) -> JoinReport:
    """Attach `market_value_eur` to player rows in place."""
    overrides = load_overrides()
    index: dict[tuple[str, str], float] = {}
    for row in values:
        index[(row["league"], normalise(row["name"]))] = row["market_value_eur"]

    by_league: dict[str, list[str]] = {}
    for league, key in index:
        by_league.setdefault(league, []).append(key)

    # Market values only exist for the seasons in the snapshot. Scoring the join
    # against every historical player-season would report a meaningless rate.
    covered = {row["season"] for row in values}
    eligible = [player for player in players if player["season"] in covered]

    report = JoinReport(total=len(eligible))
    for player in players:
        if player["season"] not in covered:
            player["market_value_eur"] = None
            continue
        league = player["league"]
        key = normalise(player["name"])
        key = overrides.get((league, key), key)

        value = index.get((league, key))
        if value is None:
            value = _fuzzy(key, by_league.get(league, []), index, league)

        if value is None:
            player["market_value_eur"] = None
            if len(report.unmatched) < 50:
                report.unmatched.append(f"{league}: {player['name']}")
        else:
            player["market_value_eur"] = value
            report.matched += 1
    return report


def _fuzzy(key: str, candidates: list[str], index: dict, league: str) -> float | None:
    best_score, best_key = 0.0, None
    for candidate in candidates:
        # Cheap length gate before the expensive comparison.
        if abs(len(candidate) - len(key)) > 6:
            continue
        score = SequenceMatcher(None, key, candidate).ratio()
        if score > best_score:
            best_score, best_key = score, candidate
    if best_key is not None and best_score >= FUZZY_FLOOR:
        return index[(league, best_key)]
    return None
