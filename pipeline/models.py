"""Three models, evaluated the way they deserve rather than the way that flatters.

Deliberate choices:

* Validation is walk-forward, never a random split. Training on 2024 to predict
  2023 leaks the future and inflates every number.
* The match model is scored against two baselines — always pick home, always pick
  the team with the better points-per-game. A model that cannot beat "always
  home" is not a model, and hiding that is the norm in this genre.
* The value model is a ridge on standardised features, chosen so its
  coefficients can be shipped to the browser and the what-if panel needs no
  server. Its residuals, not its R², are the interesting output.
* The similarity model has no ground truth and is reported as a visualisation,
  not as a model with a score.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import Ridge
from sklearn.metrics import accuracy_score, log_loss
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from .features import STYLE_FEATURES

MATCH_FEATURES = [
    "form_points_diff", "form_goals_for_diff", "form_goals_against_diff",
    "season_ppg_diff", "matches_played",
]
OUTCOMES = ["H", "D", "A"]
VALUE_FEATURES = [
    "age", "goals_p90", "assists_p90", "xg_p90", "xa_p90",
    "key_passes_p90", "minutes_share",
]


# ----------------------------------------------------------------- match model

def _one_hot_leagues(frame: pd.DataFrame, leagues: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {f"league_{lg}": (frame["league"] == lg).astype(float) for lg in leagues},
        index=frame.index,
    )


def train_match_model(features: pd.DataFrame, current_season: int, leagues: list[str]):
    """One pooled model with league as a feature.

    Five separate models would each see ~340 usable matches a season. Pooling the
    big five gives roughly five times the data, and the league indicator still
    lets the model learn that draw rates and home advantage differ.
    """
    train = features[features["season"] < current_season]
    test = features[features["season"] == current_season]

    def design(frame):
        return pd.concat([frame[MATCH_FEATURES], _one_hot_leagues(frame, leagues)], axis=1)

    model = HistGradientBoostingClassifier(
        max_depth=4, max_iter=220, learning_rate=0.06,
        l2_regularization=1.0, random_state=7,
    )
    model.fit(design(train), train["result"])

    report = {"train_matches": int(len(train)), "test_matches": int(len(test))}
    if len(test):
        probabilities = model.predict_proba(design(test))
        predicted = model.classes_[probabilities.argmax(axis=1)]
        actual = test["result"].to_numpy()

        report.update(
            accuracy=float(accuracy_score(actual, predicted)),
            log_loss=float(log_loss(actual, probabilities, labels=list(model.classes_))),
            brier=_brier(actual, probabilities, list(model.classes_)),
            baseline_home=float((actual == "H").mean()),
            baseline_form=float((np.where(test["season_ppg_diff"] >= 0, "H", "A") == actual).mean()),
            per_class=_per_class(actual, predicted),
            calibration=_calibration(actual, probabilities, list(model.classes_)),
        )
    return model, report


def _brier(actual, probabilities, classes) -> float:
    truth = np.zeros_like(probabilities)
    for row, label in enumerate(actual):
        truth[row, classes.index(label)] = 1.0
    return float(np.mean(np.sum((probabilities - truth) ** 2, axis=1)))


def _per_class(actual, predicted) -> dict:
    out = {}
    for label in OUTCOMES:
        mask = actual == label
        picked = predicted == label
        out[label] = {
            "support": int(mask.sum()),
            "recall": float((predicted[mask] == label).mean()) if mask.any() else 0.0,
            "precision": float((actual[picked] == label).mean()) if picked.any() else 0.0,
        }
    return out


def _calibration(actual, probabilities, classes, bins: int = 10) -> list[dict]:
    """Does a stated 70% actually happen 70% of the time?"""
    confidence = probabilities.max(axis=1)
    predicted = np.array(classes)[probabilities.argmax(axis=1)]
    correct = predicted == actual
    edges = np.linspace(0.3, 1.0, bins + 1)
    out = []
    for low, high in zip(edges[:-1], edges[1:]):
        mask = (confidence >= low) & (confidence < high)
        if mask.sum() >= 5:
            out.append({
                "bin": round(float((low + high) / 2), 3),
                "n": int(mask.sum()),
                "stated": float(confidence[mask].mean()),
                "observed": float(correct[mask].mean()),
            })
    return out


def predict_fixtures(model, upcoming: pd.DataFrame, leagues: list[str]) -> list[dict]:
    if upcoming.empty:
        return []
    design = pd.concat([upcoming[MATCH_FEATURES], _one_hot_leagues(upcoming, leagues)], axis=1)
    probabilities = model.predict_proba(design)
    classes = list(model.classes_)
    rows = []
    for (_, fixture), probability in zip(upcoming.iterrows(), probabilities):
        distribution = {label: float(probability[classes.index(label)]) for label in OUTCOMES}
        rows.append({
            "match_id": fixture["match_id"],
            "league": fixture["league"],
            "season": int(fixture["season"]),
            "utc_date": str(fixture["utc_date"]),
            "home_team": fixture["home_team"],
            "away_team": fixture["away_team"],
            "p_home": distribution["H"],
            "p_draw": distribution["D"],
            "p_away": distribution["A"],
            "pick": max(distribution, key=distribution.get),
            "confidence": max(distribution.values()),
        })
    return rows


# ----------------------------------------------------------------- value model

def train_value_model(players: pd.DataFrame, current_season: int, leagues: list[str]):
    frame = players[(players["season"] == current_season) & players["market_value_eur"].notna()].copy()
    frame = frame[frame["market_value_eur"] > 0]

    design = pd.concat([frame[VALUE_FEATURES], _one_hot_leagues(frame, leagues)], axis=1)
    design["age_sq"] = frame["age"] ** 2
    columns = list(design.columns)

    scaler = StandardScaler().fit(design)
    target = np.log(frame["market_value_eur"].to_numpy())

    model = Ridge(alpha=2.0).fit(scaler.transform(design), target)
    fitted = model.predict(scaler.transform(design))
    residual = target - fitted

    frame["predicted_value_eur"] = np.exp(fitted)
    frame["value_residual"] = residual
    frame["value_ratio"] = frame["market_value_eur"] / frame["predicted_value_eur"]

    # The league premium: what the market pays for identical output, by league.
    # This is the finding a single-league version of this project cannot produce.
    premium = {}
    for league in leagues:
        index = columns.index(f"league_{league}")
        effect = model.coef_[index] / scaler.scale_[index]
        premium[league] = float(np.exp(effect))
    reference = premium.get("LIGUE1") or min(premium.values())
    premium = {k: v / reference for k, v in premium.items()}

    export = {
        "columns": columns,
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
        "coef": model.coef_.tolist(),
        "intercept": float(model.intercept_),
        "r2_in_sample": float(np.corrcoef(fitted, target)[0, 1] ** 2),
        "median_abs_error_pct": float(np.median(np.abs(np.expm1(residual))) * 100),
        "league_premium": premium,
        "n": int(len(frame)),
    }
    return frame, export


# ------------------------------------------------------------ similarity model

def build_similarity(players: pd.DataFrame, current_season: int, neighbours: int, clusters: int):
    frame = players[players["season"] == current_season].copy().reset_index(drop=True)
    # Compact integer ids: the neighbour file is keyed by them, and using the
    # long source ids there roughly quadruples the payload the browser downloads.
    frame["player_id"] = np.arange(len(frame))
    matrix = frame[[f"z_{column}" for column in STYLE_FEATURES]].to_numpy()

    engine = NearestNeighbors(n_neighbors=min(neighbours + 1, len(frame)), metric="cosine").fit(matrix)
    distances, indices = engine.kneighbors(matrix)

    labels = KMeans(n_clusters=clusters, n_init=10, random_state=11).fit_predict(matrix)
    coords = PCA(n_components=2, random_state=11).fit_transform(matrix)

    frame["style_cluster"] = labels
    frame["style_x"] = coords[:, 0]
    frame["style_y"] = coords[:, 1]

    # [neighbour_id, similarity] pairs rather than objects: same information,
    # a third of the bytes.
    similar = {
        int(frame.at[row, "player_id"]): [
            [int(frame.at[int(neighbour), "player_id"]), round(float(1 - distance), 3)]
            for neighbour, distance in zip(indices[row][1:], distances[row][1:])
        ]
        for row in range(len(frame))
    }
    return frame, similar
