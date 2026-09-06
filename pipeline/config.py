"""Paths, config loading and deterministic JSON output.

Every JSON file the pipeline writes goes through `write_json`, which sorts keys
and rounds floats. Without that, a rerun on identical data produces a diff of
thousands of lines from float jitter and dict ordering, and the commit history
stops being readable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "leagues.yaml"
CACHE_DIR = ROOT / ".cache"          # raw API responses, gitignored
DATA_DIR = ROOT / "data"             # committed intermediate data
LEDGER_DIR = DATA_DIR / "ledger"     # committed, append-only, the audit trail
WEB_DATA_DIR = ROOT / "web" / "data"  # what the site actually reads

SCHEMA_VERSION = 3


@dataclass(frozen=True)
class League:
    id: str
    name: str
    country: str
    enabled: bool
    football_data_code: str
    understat_slug: str
    teams: int
    matchdays: int

    @property
    def matches_per_season(self) -> int:
        return self.teams * (self.teams - 1)


@dataclass(frozen=True)
class Config:
    leagues: list[League]
    history_seasons: list[int]
    current_season: int
    form_window: int
    neighbours: int
    style_clusters: int
    min_minutes: int

    def enabled(self) -> list[League]:
        return [lg for lg in self.leagues if lg.enabled]

    def by_id(self, league_id: str) -> League:
        for lg in self.leagues:
            if lg.id == league_id:
                return lg
        raise KeyError(f"unknown league: {league_id}")


def load_config(path: Path | None = None) -> Config:
    raw = yaml.safe_load((path or CONFIG_PATH).read_text())
    leagues = [
        League(
            id=item["id"],
            name=item["name"],
            country=item["country"],
            enabled=bool(item.get("enabled", True)),
            football_data_code=item["football_data_code"],
            understat_slug=item["understat_slug"],
            teams=int(item["teams"]),
            matchdays=int(item["matchdays"]),
        )
        for item in raw["leagues"]
    ]
    return Config(
        leagues=leagues,
        history_seasons=list(raw["seasons"]["history"]),
        current_season=int(raw["seasons"]["current"]),
        form_window=int(raw["pipeline"]["form_window"]),
        neighbours=int(raw["pipeline"]["neighbours"]),
        style_clusters=int(raw["pipeline"]["style_clusters"]),
        min_minutes=int(raw["pipeline"]["min_minutes"]),
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _round_floats(obj, places: int = 4):
    if isinstance(obj, float):
        return round(obj, places)
    if isinstance(obj, dict):
        return {k: _round_floats(v, places) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_floats(v, places) for v in obj]
    return obj


def write_json(path: Path, payload, *, places: int = 4) -> Path:
    """Write stable, diff-friendly JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(_round_floats(payload, places), indent=1, sort_keys=True, ensure_ascii=False)
    path.write_text(body + "\n", encoding="utf-8")
    return path


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def data_mode() -> str:
    """'live' when an API token is present, otherwise 'demo'.

    Demo mode generates synthetic data so a fresh clone runs end to end with no
    credentials and no network. The mode is stamped into every exported file and
    surfaced in the UI, so synthetic numbers can never be mistaken for real ones.
    """
    return "live" if os.environ.get("FOOTBALL_DATA_TOKEN") else "demo"
