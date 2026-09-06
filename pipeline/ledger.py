"""The prediction ledger.

Predictions are written before kickoff and committed. Results are scored after
the final whistle and committed separately. Neither file is ever rewritten — the
recording step refuses to overwrite an existing match_id, so a prediction cannot
be quietly improved after the fact. Git history is the proof: `git log` shows a
prediction commit dated before the match and a scoring commit dated after it.

This is the part of the project that turns "my model is 54% accurate" from a
claim into something a stranger can verify.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .config import LEDGER_DIR, utc_now_iso

PREDICTIONS = LEDGER_DIR / "predictions.jsonl"
RESULTS = LEDGER_DIR / "results.jsonl"


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def record(predictions: list[dict], *, model_version: str, mode: str) -> int:
    """Append predictions for fixtures not already in the ledger."""
    known = {row["match_id"] for row in _read(PREDICTIONS)}
    fresh = []
    for prediction in predictions:
        if prediction["match_id"] in known:
            continue
        fresh.append({**prediction, "recorded_at": utc_now_iso(),
                      "model_version": model_version, "mode": mode})
    _append(PREDICTIONS, fresh)
    return len(fresh)


def score(finished: dict[str, dict]) -> int:
    """Score any recorded prediction whose match has now finished.

    `finished` maps match_id to a row with home_goals and away_goals.
    """
    already = {row["match_id"] for row in _read(RESULTS)}
    rows = []
    for prediction in _read(PREDICTIONS):
        match_id = prediction["match_id"]
        if match_id in already or match_id not in finished:
            continue
        match = finished[match_id]
        home, away = match["home_goals"], match["away_goals"]
        if home is None or away is None:
            continue
        actual = "H" if home > away else "D" if home == away else "A"
        probability = {"H": prediction["p_home"], "D": prediction["p_draw"], "A": prediction["p_away"]}
        rows.append({
            "match_id": match_id,
            "league": prediction["league"],
            "utc_date": prediction["utc_date"],
            "home_team": prediction["home_team"],
            "away_team": prediction["away_team"],
            "pick": prediction["pick"],
            "confidence": prediction["confidence"],
            "actual": actual,
            "score": f"{home}-{away}",
            "correct": bool(prediction["pick"] == actual),
            "brier": float(sum((probability[k] - (k == actual)) ** 2 for k in "HDA")),
            "scored_at": utc_now_iso(),
        })
    _append(RESULTS, rows)
    return len(rows)


def summary(recent: int = 240) -> dict:
    rows = sorted(_read(RESULTS), key=lambda row: row["utc_date"])
    if not rows:
        return {"n": 0, "cells": [], "by_league": {}, "rolling": []}

    correct = np.array([row["correct"] for row in rows], dtype=float)
    brier = np.array([row["brier"] for row in rows], dtype=float)

    by_league = {}
    for league in sorted({row["league"] for row in rows}):
        subset = [row for row in rows if row["league"] == league]
        by_league[league] = {
            "n": len(subset),
            "accuracy": float(np.mean([row["correct"] for row in subset])),
            "brier": float(np.mean([row["brier"] for row in subset])),
        }

    window = 40
    rolling = [
        {"index": i + 1, "accuracy": float(correct[max(0, i - window + 1): i + 1].mean())}
        for i in range(len(correct))
        if i >= window - 1
    ]

    cells = [
        {
            "date": row["utc_date"][:10],
            "league": row["league"],
            "pick": row["pick"],
            "actual": row["actual"],
            "correct": row["correct"],
            "confidence": round(row["confidence"], 3),
            "label": f"{row['home_team']} {row['score']} {row['away_team']}",
        }
        for row in rows[-recent:]
    ]

    return {
        "n": len(rows),
        "accuracy": float(correct.mean()),
        "brier": float(brier.mean()),
        "accuracy_last_50": float(correct[-50:].mean()),
        "first_recorded": rows[0]["utc_date"][:10],
        "last_scored": rows[-1]["utc_date"][:10],
        "by_league": by_league,
        "rolling": rolling[-160:],
        "cells": cells,
    }
