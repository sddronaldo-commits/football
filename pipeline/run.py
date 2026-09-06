"""Run the whole pipeline.

    python -m pipeline.run                 # demo mode unless a token is present
    python -m pipeline.run --score-only    # score finished matches, no retrain
    python -m pipeline.run --backfill      # demo only: fill the ledger for a demo wall

The weekly workflow calls it plainly; the Tuesday workflow calls --score-only.
"""

from __future__ import annotations

import argparse
import hashlib
import sys

import pandas as pd

from . import crosswalk, export, features, ledger, models
from .config import DATA_DIR, load_config, data_mode, utc_now_iso, write_json
from .sources import demo, live


def _model_version(config, mode: str) -> str:
    seed = f"{mode}-{config.current_season}-{'-'.join(lg.id for lg in config.enabled())}"
    return f"v{hashlib.sha1(seed.encode()).hexdigest()[:7]}"


def collect(config, mode: str):
    if mode == "live":
        matches, players = live.collect(config)
        values = _load_market_values()
    else:
        matches, players, values = demo.generate(config)
    return matches, players, values


def _load_market_values() -> list[dict]:
    """Committed Transfermarkt-derived snapshot. Refreshed monthly, by hand."""
    path = DATA_DIR / "market_values.json"
    if not path.exists():
        raise FileNotFoundError(
            "data/market_values.json is missing. Live mode needs the market value "
            "snapshot; see README > Data sources."
        )
    import json
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Big Five football lab pipeline")
    parser.add_argument("--score-only", action="store_true", help="score finished matches only")
    parser.add_argument("--backfill", action="store_true",
                        help="demo only: seed the ledger from played matches")
    args = parser.parse_args(argv)

    config = load_config()
    mode = data_mode()
    version = _model_version(config, mode)
    league_ids = [lg.id for lg in config.enabled()]
    print(f"mode={mode} model={version} leagues={','.join(league_ids)}")

    matches, players, values = collect(config, mode)
    print(f"collected {len(matches)} matches, {len(players)} player-seasons")

    join = crosswalk.attach_market_values(players, values)
    print(f"market value join: {join.match_rate:.1%} of player-seasons")
    # Written every run and uploaded as a CI artifact: when a source changes its
    # name formatting, this file is what you edit config/overrides.csv from.
    write_json(DATA_DIR / "join_report.json", {
        "at": utc_now_iso(),
        "mode": mode,
        "match_rate": round(join.match_rate, 4),
        "matched": join.matched,
        "total": join.total,
        "unmatched_sample": join.unmatched,
    })

    team_match = features.build_team_match(matches, config.form_window)
    match_features = features.build_match_features(team_match)
    player_season = features.build_player_season(players, config.min_minutes)

    finished = {
        row["match_id"]: row for row in matches
        if row["status"] == "FINISHED" and row["home_goals"] is not None
    }
    scored = ledger.score(finished)
    print(f"ledger: scored {scored} previously recorded predictions")

    if args.score_only:
        summary = ledger.summary()
        write_json(DATA_DIR / "last_run.json",
                   {"at": utc_now_iso(), "step": "score-only", "scored": scored})
        print(f"ledger now holds {summary['n']} scored predictions")
        return 0

    model, match_report = models.train_match_model(
        match_features, config.current_season, league_ids)
    match_report["model_version"] = version
    if "accuracy" in match_report:
        print(f"match model: {match_report['accuracy']:.3f} accuracy "
              f"vs {match_report['baseline_home']:.3f} always-home")

    upcoming = _upcoming(matches, team_match, config, league_ids)
    fixtures = models.predict_fixtures(model, upcoming, league_ids)
    recorded = ledger.record(fixtures, model_version=version, mode=mode)
    print(f"ledger: recorded {recorded} new predictions for {len(fixtures)} upcoming fixtures")

    if args.backfill:
        if mode != "demo":
            print("refusing to backfill in live mode: the ledger only grows forward", file=sys.stderr)
            return 2
        added = _backfill(model, match_features, config, league_ids, version)
        print(f"ledger: backfilled {added} demo predictions")
        ledger.score(finished)

    valued, value_export = models.train_value_model(player_season, config.current_season, league_ids)
    styled, similar = models.build_similarity(
        player_season, config.current_season, config.neighbours, config.style_clusters)

    merged = styled.merge(
        valued[["source_id", "predicted_value_eur", "value_residual", "value_ratio"]],
        on="source_id", how="left")

    written = export.export_all(
        config, mode=mode, model_version=version, players=merged, similar=similar,
        value_export=value_export, match_report=match_report, fixtures=fixtures,
        ledger_summary=ledger.summary(), team_match=team_match, join_rate=join.match_rate)

    write_json(DATA_DIR / "last_run.json", {
        "at": utc_now_iso(), "step": "full", "mode": mode, "model_version": version,
        "matches": len(matches), "players": int(len(player_season)),
        "join_rate": round(join.match_rate, 4),
    })
    print("wrote:\n  " + "\n  ".join(written))
    return 0


def _upcoming(matches, team_match, config, league_ids) -> pd.DataFrame:
    """Feature rows for the next unplayed matchday in each league.

    Form features come from matches already played, so a scheduled fixture is
    described by each side's most recent state.
    """
    scheduled = pd.DataFrame([
        row for row in matches
        if row["status"] != "FINISHED" and row["season"] == config.current_season
    ])
    if scheduled.empty:
        return scheduled

    scheduled["utc_date"] = pd.to_datetime(scheduled["utc_date"], format="mixed", utc=True)
    scheduled = scheduled.sort_values("utc_date")
    scheduled = scheduled.groupby("league", group_keys=False).head(config.leagues[0].teams // 2 * 2)

    latest = (team_match.sort_values("utc_date")
              .groupby(["league", "team"], as_index=False)
              .last()[["league", "team", "form_points", "form_goals_for",
                       "form_goals_against", "season_ppg", "matches_played"]])

    frame = scheduled.merge(latest.add_suffix("_home").rename(
        columns={"league_home": "league", "team_home": "home_team"}),
        on=["league", "home_team"], how="left")
    frame = frame.merge(latest.add_suffix("_away").rename(
        columns={"league_away": "league", "team_away": "away_team"}),
        on=["league", "away_team"], how="left")

    for column in ("form_points", "form_goals_for", "form_goals_against", "season_ppg"):
        frame[f"{column}_diff"] = frame[f"{column}_home"] - frame[f"{column}_away"]
    frame["matches_played"] = frame[["matches_played_home", "matches_played_away"]].min(axis=1)
    return frame.dropna(subset=["form_points_diff", "season_ppg_diff"])


def _backfill(model, match_features, config, league_ids, version) -> int:
    """Seed the demo ledger with out-of-sample predictions on played matches.

    Only ever runs in demo mode. These are genuine walk-forward predictions — the
    model never saw the current season during training — but they were not made
    before kickoff, so they are marked `mode: demo` and the site labels them.
    """
    played = match_features[match_features["season"] == config.current_season]
    if played.empty:
        return 0
    predictions = models.predict_fixtures(model, played.assign(
        utc_date=played["utc_date"].astype(str)), league_ids)
    return ledger.record(predictions, model_version=version, mode="demo")


if __name__ == "__main__":
    raise SystemExit(main())
