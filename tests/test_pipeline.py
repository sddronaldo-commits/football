"""Tests aimed at the ways this pipeline actually breaks.

Not coverage theatre: each test corresponds to a real failure that would either
corrupt the ledger or silently publish wrong numbers.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from pipeline import crosswalk, features, ledger, models
from pipeline.config import WEB_DATA_DIR, load_config
from pipeline.sources import demo, live


@pytest.fixture(scope="module")
def config():
    return load_config()


@pytest.fixture(scope="module")
def generated(config):
    return demo.generate(config, seed=1234)


def test_every_league_produces_the_right_number_of_matches(config, generated):
    matches, _, _ = generated
    frame = pd.DataFrame(matches)
    for league in config.enabled():
        subset = frame[(frame["league"] == league.id) & (frame["season"] == config.history_seasons[0])]
        # Ligue 1 and the Bundesliga play 34 matchdays, not 38. Hardcoding the
        # Premier League's shape is the classic multi-league bug.
        assert len(subset) == league.matches_per_season, league.id


def test_form_features_never_include_the_current_result(config, generated):
    matches, _, _ = generated
    team_match = features.build_team_match(matches, config.form_window)
    first = team_match.groupby(["league", "season", "team"]).head(1)
    # A team's first match of a season has no prior form to look back on.
    assert first["form_points"].isna().all()
    assert (team_match["matches_played"] >= 0).all()


def test_player_features_are_comparable_across_leagues(config, generated):
    _, players, values = generated
    crosswalk.attach_market_values(players, values)
    frame = features.build_player_season(players, config.min_minutes)
    grouped = frame.groupby(["league", "season"])["z_goals_p90"]
    assert grouped.mean().abs().max() < 1e-6
    assert (grouped.std() - 1).abs().max() < 0.05


def test_crosswalk_folds_accents_and_punctuation():
    assert crosswalk.normalise("Vinícius Júnior") == crosswalk.normalise("Vinicius Junior")
    assert crosswalk.normalise("N'Golo  Kanté") == "n golo kante"


def test_market_value_join_rate_stays_high(config, generated):
    _, players, values = generated
    report = crosswalk.attach_market_values(players, values)
    # A formatting change at any source shows up here before it reaches the site.
    assert report.match_rate > 0.95, report.unmatched[:10]


def test_match_model_beats_always_predict_home(config, generated):
    matches, _, _ = generated
    team_match = features.build_team_match(matches, config.form_window)
    match_features = features.build_match_features(team_match)
    _, report = models.train_match_model(
        match_features, config.current_season, [lg.id for lg in config.enabled()])
    assert report["accuracy"] > report["baseline_home"]
    assert 0.3 < report["accuracy"] < 0.75, "suspiciously good means a leak"


def test_ledger_never_overwrites_a_recorded_prediction(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "PREDICTIONS", tmp_path / "predictions.jsonl")
    monkeypatch.setattr(ledger, "RESULTS", tmp_path / "results.jsonl")
    prediction = {
        "match_id": "EPL-1", "league": "EPL", "season": 2025, "utc_date": "2025-09-06T14:00:00+00:00",
        "home_team": "Arsenal", "away_team": "Chelsea",
        "p_home": 0.5, "p_draw": 0.25, "p_away": 0.25, "pick": "H", "confidence": 0.5,
    }
    assert ledger.record([prediction], model_version="v1", mode="demo") == 1
    tampered = {**prediction, "pick": "A", "p_away": 0.9}
    assert ledger.record([tampered], model_version="v2", mode="demo") == 0

    lines = (tmp_path / "predictions.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["pick"] == "H"


def test_ledger_scores_against_the_real_result(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "PREDICTIONS", tmp_path / "predictions.jsonl")
    monkeypatch.setattr(ledger, "RESULTS", tmp_path / "results.jsonl")
    ledger.record([{
        "match_id": "EPL-1", "league": "EPL", "season": 2025, "utc_date": "2025-09-06T14:00:00+00:00",
        "home_team": "Arsenal", "away_team": "Chelsea",
        "p_home": 0.6, "p_draw": 0.2, "p_away": 0.2, "pick": "H", "confidence": 0.6,
    }], model_version="v1", mode="demo")

    scored = ledger.score({"EPL-1": {"home_goals": 1, "away_goals": 3}})
    assert scored == 1
    summary = ledger.summary()
    assert summary["n"] == 1 and summary["accuracy"] == 0.0
    assert ledger.score({"EPL-1": {"home_goals": 1, "away_goals": 3}}) == 0


def test_understat_parser_handles_the_embedded_payload():
    html = r"""<script>var playersData = JSON.parse('[{"id":"1","player_name":"Test"}]');</script>"""
    assert live.parse_understat_payload(html, "playersData")[0]["player_name"] == "Test"
    with pytest.raises(ValueError):
        live.parse_understat_payload("<script>nothing here</script>", "playersData")


@pytest.mark.skipif(not (WEB_DATA_DIR / "meta.json").exists(), reason="pipeline has not run")
def test_exported_files_are_shaped_the_way_the_site_expects():
    meta = json.loads((WEB_DATA_DIR / "meta.json").read_text())
    assert meta["mode"] in {"demo", "live"}
    assert meta["schema"] >= 3

    players = json.loads((WEB_DATA_DIR / "players.json").read_text())
    assert {"name", "league", "style_x"} <= set(players["columns"])
    assert all(len(row) == len(players["columns"]) for row in players["rows"][:200])

    ledger_summary = json.loads((WEB_DATA_DIR / "ledger.json").read_text())
    if ledger_summary["n"]:
        assert 0 <= ledger_summary["accuracy"] <= 1
