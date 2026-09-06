"""Everything the website reads, written as static files.

The site has no backend. Each file here is fetched directly by the browser, so
they are kept small and split by concern: the homepage only needs meta, ledger
and fixtures, and the heavier player payload loads when the scouting view opens.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import SCHEMA_VERSION, WEB_DATA_DIR, Config, utc_now_iso, write_json

PLAYER_COLUMNS = [
    "player_id", "name", "team", "league", "position", "age", "minutes",
    "goals", "assists", "goals_p90", "assists_p90", "xg_p90", "xa_p90",
    "key_passes_p90", "shots_p90", "finishing_delta", "market_value_eur",
    "predicted_value_eur", "value_residual", "value_ratio",
    "style_cluster", "style_x", "style_y",
]


def export_all(config: Config, *, mode: str, model_version: str, players: pd.DataFrame,
               similar: dict, value_export: dict, match_report: dict, fixtures: list[dict],
               ledger_summary: dict, team_match: pd.DataFrame, join_rate: float) -> list[str]:
    written = []

    written.append(write_json(WEB_DATA_DIR / "meta.json", {
        "schema": SCHEMA_VERSION,
        "mode": mode,
        "model_version": model_version,
        "generated_at": utc_now_iso(),
        "current_season": config.current_season,
        "history_seasons": config.history_seasons,
        "join_rate": round(join_rate, 4),
        "leagues": [
            {"id": lg.id, "name": lg.name, "country": lg.country, "teams": lg.teams}
            for lg in config.enabled()
        ],
    }))

    written.append(write_json(WEB_DATA_DIR / "ledger.json", ledger_summary))
    written.append(write_json(WEB_DATA_DIR / "fixtures.json", {
        "generated_at": utc_now_iso(),
        "fixtures": sorted(fixtures, key=lambda row: (row["utc_date"], row["league"]))[:60],
    }))
    written.append(write_json(WEB_DATA_DIR / "model_match.json", match_report))
    written.append(write_json(WEB_DATA_DIR / "model_value.json", value_export))

    frame = players.copy()
    frame = frame[[column for column in PLAYER_COLUMNS if column in frame.columns]]
    frame = frame.replace({np.nan: None})
    written.append(write_json(WEB_DATA_DIR / "players.json", {
        "columns": list(frame.columns),
        "rows": frame.round(4).values.tolist(),
    }))
    # Sharded per league: the scouting view loads one league at a time instead of
    # pulling every neighbour list in the big five up front.
    league_of = dict(zip(players["player_id"], players["league"]))
    shards: dict[str, dict] = {}
    for player_id, neighbours in similar.items():
        shards.setdefault(league_of.get(player_id, "OTHER"), {})[player_id] = neighbours
    for league_id, shard in shards.items():
        written.append(write_json(WEB_DATA_DIR / "similar" / f"{league_id}.json", shard))
    written.append(write_json(WEB_DATA_DIR / "analytics.json",
                              league_analytics(team_match, players, value_export)))
    return [str(path) for path in written]


def league_analytics(team_match: pd.DataFrame, players: pd.DataFrame, value_export: dict) -> dict:
    """Descriptive analytics that only exist because five leagues are pooled."""
    home = team_match[team_match["is_home"] == 1]

    rows = []
    for league, group in home.groupby("league"):
        total = len(group)
        draws = (group["goals_for"] == group["goals_against"]).mean()
        home_wins = (group["goals_for"] > group["goals_against"]).mean()
        away_wins = (group["goals_for"] < group["goals_against"]).mean()
        rows.append({
            "league": league,
            "matches": int(total),
            "goals_per_match": float((group["goals_for"] + group["goals_against"]).mean()),
            "home_win_rate": float(home_wins),
            "draw_rate": float(draws),
            "away_win_rate": float(away_wins),
            # Points per match won by the home side above a neutral 1.5 baseline.
            "home_advantage_points": float(
                (3 * home_wins + draws) - (3 * away_wins + draws)
            ),
            "value_premium": value_export["league_premium"].get(league),
        })

    current = players[players["market_value_eur"].notna()]
    top_over = _extremes(current, ascending=False)
    top_under = _extremes(current, ascending=True)

    return {
        "leagues": sorted(rows, key=lambda row: row["league"]),
        "premium_reference": "LIGUE1",
        "overvalued": top_over,
        "undervalued": top_under,
    }


def _extremes(frame: pd.DataFrame, *, ascending: bool, limit: int = 12) -> list[dict]:
    ordered = frame.sort_values("value_residual", ascending=ascending).head(limit)
    return [
        {
            "player_id": row["player_id"],
            "name": row["name"],
            "team": row["team"],
            "league": row["league"],
            "position": row["position"],
            "age": int(row["age"]),
            "market_value_eur": float(row["market_value_eur"]),
            "predicted_value_eur": float(row["predicted_value_eur"]),
            "value_ratio": float(row["value_ratio"]),
        }
        for _, row in ordered.iterrows()
    ]
