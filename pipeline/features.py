"""The two tables everything else reads.

`team_match`: one row per team per match, with rolling form computed only from
matches already played at that point. Every rolling feature is shifted by one
match — a form column that includes the current result leaks the label, and a
model trained on it looks brilliant and predicts nothing.

`player_season`: one row per player-season, per-90 rates plus within-league-season
z-scores. Raw per-90 numbers are not comparable across leagues: without the
z-scoring, a similarity search returns whichever league scores most goals.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STYLE_FEATURES = [
    "goals_p90", "assists_p90", "shots_p90", "key_passes_p90",
    "xg_p90", "xa_p90", "cards_p90", "minutes_share",
]


def build_team_match(matches: list[dict], form_window: int) -> pd.DataFrame:
    frame = pd.DataFrame(matches)
    played = frame[frame["status"] == "FINISHED"].copy()

    long = []
    for side, opponent in (("home", "away"), ("away", "home")):
        part = played.rename(
            columns={
                f"{side}_team": "team",
                f"{opponent}_team": "opponent",
                f"{side}_goals": "goals_for",
                f"{opponent}_goals": "goals_against",
            }
        )[["league", "season", "match_id", "utc_date", "matchday", "team", "opponent",
           "goals_for", "goals_against"]].copy()
        part["is_home"] = int(side == "home")
        long.append(part)

    table = pd.concat(long, ignore_index=True)
    table["utc_date"] = pd.to_datetime(table["utc_date"], format="mixed", utc=True)
    table = table.sort_values(["league", "team", "utc_date"]).reset_index(drop=True)

    table["result"] = np.select(
        [table["goals_for"] > table["goals_against"], table["goals_for"] == table["goals_against"]],
        ["W", "D"],
        default="L",
    )
    table["points"] = table["result"].map({"W": 3, "D": 1, "L": 0})
    table["goal_diff"] = table["goals_for"] - table["goals_against"]

    grouped = table.groupby(["league", "season", "team"], sort=False)
    for column, source in (
        ("form_points", "points"),
        ("form_goals_for", "goals_for"),
        ("form_goals_against", "goals_against"),
    ):
        # shift(1) first: the current match must not see its own result.
        table[column] = (
            grouped[source]
            .transform(lambda values: values.shift(1).rolling(form_window, min_periods=1).mean())
        )
    table["matches_played"] = grouped.cumcount()
    table["season_ppg"] = (
        grouped["points"].transform(lambda values: values.shift(1).expanding().mean())
    )
    return table


def build_match_features(team_match: pd.DataFrame) -> pd.DataFrame:
    """One row per match: home features minus away features."""
    home = team_match[team_match["is_home"] == 1]
    away = team_match[team_match["is_home"] == 0]
    keys = ["match_id", "league", "season", "utc_date", "matchday"]
    merged = home.merge(away, on=keys, suffixes=("_home", "_away"))

    features = merged[keys].copy()
    features["home_team"] = merged["team_home"]
    features["away_team"] = merged["team_away"]
    for column in ("form_points", "form_goals_for", "form_goals_against", "season_ppg"):
        features[f"{column}_diff"] = merged[f"{column}_home"] - merged[f"{column}_away"]
    features["matches_played"] = merged[["matches_played_home", "matches_played_away"]].min(axis=1)
    features["result"] = np.select(
        [merged["goals_for_home"] > merged["goals_for_away"],
         merged["goals_for_home"] == merged["goals_for_away"]],
        ["H", "D"],
        default="A",
    )
    return features.dropna(subset=[c for c in features.columns if c.endswith("_diff")])


def build_player_season(players: list[dict], min_minutes: int) -> pd.DataFrame:
    frame = pd.DataFrame(players)
    frame = frame[frame["minutes"] >= min_minutes].copy()
    nineties = frame["minutes"] / 90

    frame["goals_p90"] = frame["goals"] / nineties
    frame["assists_p90"] = frame["assists"] / nineties
    frame["shots_p90"] = frame["shots"] / nineties
    frame["key_passes_p90"] = frame["key_passes"] / nineties
    frame["xg_p90"] = frame["xg"] / nineties
    frame["xa_p90"] = frame["xa"] / nineties
    frame["cards_p90"] = (frame["yellow"] + 3 * frame["red"]) / nineties
    frame["minutes_share"] = frame["minutes"] / frame.groupby(["league", "season"])["minutes"].transform("max")
    frame["goal_contribution_p90"] = frame["goals_p90"] + frame["assists_p90"]
    frame["finishing_delta"] = frame["goals"] - frame["xg"]

    # Comparability across leagues: z-score inside each league-season.
    for column in STYLE_FEATURES:
        grouped = frame.groupby(["league", "season"])[column]
        frame[f"z_{column}"] = ((frame[column] - grouped.transform("mean"))
                                / grouped.transform("std").replace(0, np.nan)).fillna(0.0)
    return frame.reset_index(drop=True)
