"""Live sources.

Matches come from football-data.org, whose free tier covers exactly the big five
plus the Champions League and a few others, at 10 requests per minute, with
fixtures, results and tables but no player-level statistics.

Player statistics come from Understat, which covers precisely the big five and
publishes shot-level expected goals. Understat is a scrape, not an API: the page
embeds its payload in a `JSON.parse('...')` call inside a script tag. That is a
fragile contract, so `parse_understat_payload` is tested against a fixture and
the pipeline fails loudly rather than silently exporting an empty league.
"""

from __future__ import annotations

import codecs
import json
import os
import re

from ..config import Config, League
from ..http import Fetcher

FD_BASE = "https://api.football-data.org/v4"
UNDERSTAT_BASE = "https://understat.com/league"

_PAYLOAD = re.compile(r"JSON\.parse\('(?P<body>.*?)'\)", re.DOTALL)


def parse_understat_payload(html: str, variable: str) -> list[dict]:
    """Pull one hex-escaped JSON blob out of an Understat page."""
    for match in _PAYLOAD.finditer(html):
        start = max(0, match.start() - 120)
        if variable in html[start:match.start()]:
            body = codecs.decode(match.group("body"), "unicode_escape")
            return json.loads(body)
    raise ValueError(f"Understat payload '{variable}' not found — the page layout changed")


def fetch_matches(fetcher: Fetcher, league: League, season: int) -> list[dict]:
    token = os.environ.get("FOOTBALL_DATA_TOKEN")
    if not token:
        raise RuntimeError("FOOTBALL_DATA_TOKEN is not set")
    url = f"{FD_BASE}/competitions/{league.football_data_code}/matches?season={season}"
    payload = json.loads(fetcher.get(url, headers={"X-Auth-Token": token}))

    rows = []
    for match in payload.get("matches", []):
        score = match.get("score", {}).get("fullTime", {})
        rows.append(
            {
                "league": league.id,
                "season": season,
                "match_id": f"{league.id}-{match['id']}",
                "utc_date": match["utcDate"],
                "matchday": match.get("matchday"),
                "status": match["status"],
                "home_team": match["homeTeam"]["name"],
                "away_team": match["awayTeam"]["name"],
                "home_goals": score.get("home"),
                "away_goals": score.get("away"),
            }
        )
    return rows


def fetch_players(fetcher: Fetcher, league: League, season: int) -> list[dict]:
    html = fetcher.get(f"{UNDERSTAT_BASE}/{league.understat_slug}/{season}")
    rows = []
    for player in parse_understat_payload(html, "playersData"):
        rows.append(
            {
                "league": league.id,
                "season": season,
                "source_id": f"understat-{player['id']}",
                "name": player["player_name"],
                "team": player["team_title"],
                "position": (player.get("position") or "").split(" ")[0] or "M",
                "minutes": int(player["time"]),
                "goals": int(player["goals"]),
                "assists": int(player["assists"]),
                "shots": int(player["shots"]),
                "key_passes": int(player["key_passes"]),
                "xg": float(player["xG"]),
                "xa": float(player["xA"]),
                "yellow": int(player.get("yellow_cards", 0)),
                "red": int(player.get("red_cards", 0)),
            }
        )
    return rows


def collect(config: Config) -> tuple[list[dict], list[dict]]:
    fetcher = Fetcher()
    matches: list[dict] = []
    players: list[dict] = []
    seasons = config.history_seasons + [config.current_season]
    for league in config.enabled():
        for season in seasons:
            matches.extend(fetch_matches(fetcher, league, season))
            players.extend(fetch_players(fetcher, league, season))
    return matches, players
