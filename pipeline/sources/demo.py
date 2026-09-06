"""Synthetic data, for running the whole pipeline with no token and no network.

Everything here is generated from a seeded random process: real club names, but
invented players and invented results. It exists so that `make demo` works on a
fresh clone, so tests are deterministic, and so contributors can develop against
a full dataset without burning free-tier quota.

Demo output is stamped `mode: demo` in every exported file and the site shows a
banner. Synthetic numbers should never be able to pass as real ones.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from ..config import Config, League

CLUBS = {
    "EPL": ["Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton", "Chelsea",
            "Crystal Palace", "Everton", "Fulham", "Ipswich Town", "Leicester City",
            "Liverpool", "Manchester City", "Manchester United", "Newcastle United",
            "Nottingham Forest", "Southampton", "Tottenham Hotspur", "West Ham United",
            "Wolverhampton Wanderers"],
    "LALIGA": ["Alaves", "Athletic Club", "Atletico Madrid", "Barcelona", "Celta Vigo",
               "Espanyol", "Getafe", "Girona", "Las Palmas", "Leganes", "Mallorca",
               "Osasuna", "Rayo Vallecano", "Real Betis", "Real Madrid", "Real Sociedad",
               "Sevilla", "Valencia", "Valladolid", "Villarreal"],
    "SERIEA": ["Atalanta", "Bologna", "Cagliari", "Como", "Empoli", "Fiorentina", "Genoa",
               "Hellas Verona", "Inter", "Juventus", "Lazio", "Lecce", "AC Milan", "Monza",
               "Napoli", "Parma", "Roma", "Torino", "Udinese", "Venezia"],
    "BUNDESLIGA": ["Augsburg", "Bayer Leverkusen", "Bayern Munich", "Bochum",
                   "Borussia Dortmund", "Borussia Monchengladbach", "Eintracht Frankfurt",
                   "Freiburg", "Heidenheim", "Hoffenheim", "Holstein Kiel", "Mainz 05",
                   "RB Leipzig", "St. Pauli", "Stuttgart", "Union Berlin", "Werder Bremen",
                   "Wolfsburg"],
    "LIGUE1": ["Angers", "Auxerre", "Brest", "Le Havre", "Lens", "Lille", "Lyon",
               "Marseille", "Monaco", "Montpellier", "Nantes", "Nice", "Paris Saint-Germain",
               "Reims", "Rennes", "Saint-Etienne", "Strasbourg", "Toulouse"],
}

# Invented players. Deliberately not real footballers: attaching synthetic
# statistics to real names would be misleading even in a demo.
FIRST = ["Aleks", "Bram", "Cato", "Davor", "Emre", "Fabio", "Goran", "Hugo", "Ilias",
         "Jonas", "Kadir", "Lars", "Milo", "Nuno", "Osman", "Pavel", "Quim", "Rafa",
         "Stig", "Tomas", "Ugo", "Viktor", "Wim", "Yannis", "Zeno", "Andrei", "Bo",
         "Cesar", "Dries", "Eloy", "Ferran", "Gil", "Halim", "Ivo", "Joris", "Kai"]
LAST = ["Aleman", "Brandt", "Castellan", "Duarte", "Ekberg", "Ferrero", "Grimaldi",
        "Halvorsen", "Iversen", "Jansen", "Kovac", "Lindqvist", "Moretti", "Novak",
        "Oyelaran", "Petrov", "Quintero", "Rossi", "Sandoval", "Thoresen", "Urbina",
        "Vasquez", "Weiss", "Xhemaili", "Yildiz", "Zoric", "Abadi", "Beaumont",
        "Cisse", "Delgado", "Engels", "Fontaine", "Girard", "Hoffmann", "Ionescu"]
LAST = [name.encode("ascii", "ignore").decode() for name in LAST]

POSITIONS = ["F", "M", "D", "GK"]
POSITION_WEIGHTS = [0.22, 0.36, 0.34, 0.08]

# The market premium the demo bakes in, so the league-premium analysis has
# something real to recover. Roughly ordered like the actual transfer market.
LEAGUE_PREMIUM = {"EPL": 1.55, "LALIGA": 1.18, "SERIEA": 1.02, "BUNDESLIGA": 1.10, "LIGUE1": 0.82}
# Home advantage genuinely varies by league; the match model should find this.
HOME_EDGE = {"EPL": 0.22, "LALIGA": 0.27, "SERIEA": 0.24, "BUNDESLIGA": 0.20, "LIGUE1": 0.25}


def _season_start(season: int) -> datetime:
    return datetime(season, 8, 12, tzinfo=timezone.utc)


def _fixtures(clubs: list[str]) -> list[tuple[int, str, str]]:
    """Round-robin, home and away, via the circle method."""
    teams = list(clubs)
    n = len(teams)
    rounds = []
    order = teams[:]
    for r in range(n - 1):
        pairs = [(order[i], order[n - 1 - i]) for i in range(n // 2)]
        rounds.append([(h, a) if r % 2 == 0 else (a, h) for h, a in pairs])
        order = [order[0]] + [order[-1]] + order[1:-1]
    schedule = []
    for matchday, pairs in enumerate(rounds, start=1):
        for home, away in pairs:
            schedule.append((matchday, home, away))
    for matchday, pairs in enumerate(rounds, start=n):
        for home, away in pairs:
            schedule.append((matchday, away, home))
    return schedule


def _strengths(rng: np.random.Generator, clubs: list[str]) -> dict[str, float]:
    # A few strong clubs, a long tail — closer to reality than a normal draw.
    raw = rng.gamma(shape=3.0, scale=0.34, size=len(clubs))
    raw = (raw - raw.mean()) / raw.std()
    return {club: float(value) for club, value in zip(clubs, raw)}


def generate(config: Config, *, seed: int = 20260906, played_fraction: float = 0.55):
    rng = np.random.default_rng(seed)
    matches: list[dict] = []
    players: list[dict] = []
    seasons = config.history_seasons + [config.current_season]

    for league in config.enabled():
        clubs = CLUBS[league.id][: league.teams]
        base = _strengths(rng, clubs)

        for season in seasons:
            drift = {c: base[c] + rng.normal(0, 0.25) for c in clubs}
            attack = {c: 0.30 * drift[c] for c in clubs}
            defence = {c: -0.26 * drift[c] for c in clubs}
            schedule = _fixtures(clubs)
            start = _season_start(season)
            is_current = season == config.current_season
            cutoff = int(len(schedule) * played_fraction) if is_current else len(schedule)

            for index, (matchday, home, away) in enumerate(schedule):
                kickoff = start + timedelta(days=(matchday - 1) * 7, hours=int(rng.integers(12, 21)))
                played = index < cutoff
                row = {
                    "league": league.id,
                    "season": season,
                    "match_id": f"{league.id}-{season}-{index:04d}",
                    "utc_date": kickoff.isoformat(),
                    "matchday": matchday,
                    "status": "FINISHED" if played else "SCHEDULED",
                    "home_team": home,
                    "away_team": away,
                    "home_goals": None,
                    "away_goals": None,
                }
                if played:
                    lam_home = np.exp(0.16 + HOME_EDGE[league.id] + attack[home] + defence[away])
                    lam_away = np.exp(0.16 + attack[away] + defence[home])
                    row["home_goals"] = int(rng.poisson(lam_home))
                    row["away_goals"] = int(rng.poisson(lam_away))
                matches.append(row)

            for club in clubs:
                squad = int(rng.integers(21, 26))
                for slot in range(squad):
                    position = str(rng.choice(POSITIONS, p=POSITION_WEIGHTS))
                    minutes = int(np.clip(rng.normal(1750, 780), 90, league.matchdays * 90))
                    quality = drift[club] * 0.55 + rng.normal(0, 0.85)
                    per90 = np.exp(quality * 0.42)
                    goal_rate = {"F": 0.42, "M": 0.16, "D": 0.05, "GK": 0.0}[position] * per90
                    assist_rate = {"F": 0.18, "M": 0.22, "D": 0.08, "GK": 0.01}[position] * per90
                    nineties = minutes / 90
                    goals = int(rng.poisson(goal_rate * nineties))
                    assists = int(rng.poisson(assist_rate * nineties))
                    players.append(
                        {
                            "league": league.id,
                            "season": season,
                            "source_id": f"demo-{league.id}-{season}-{club[:3]}-{slot:02d}",
                            "name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                            "team": club,
                            "position": position,
                            "age": int(np.clip(rng.normal(26, 4), 17, 39)),
                            "minutes": minutes,
                            "goals": goals,
                            "assists": assists,
                            "shots": int(rng.poisson(max(goals * 6.5, 2))),
                            "key_passes": int(rng.poisson(max(assists * 7.0, 3))),
                            "xg": float(max(0.0, rng.normal(goals * 0.94, 1.1))),
                            "xa": float(max(0.0, rng.normal(assists * 0.96, 0.9))),
                            "yellow": int(rng.poisson(2.4)),
                            "red": int(rng.poisson(0.12)),
                            "_quality": float(quality),
                            "_club_strength": float(drift[club]),
                        }
                    )

    values = _market_values(rng, players, config.current_season)
    return matches, players, values


def _market_values(rng, players, current_season) -> list[dict]:
    """Stand-in for the Transfermarkt dataset.

    Deliberately generated the way the real market behaves: driven by age, club
    prestige and league, with output mattering less than people assume. This is
    what makes the value model's honest framing necessary rather than decorative.
    """
    rows = []
    for player in players:
        if player["season"] != current_season:
            continue
        age = player["age"]
        age_curve = -0.012 * (age - 24.5) ** 2
        positional = {"F": 0.30, "M": 0.12, "D": 0.0, "GK": -0.22}[player["position"]]
        minutes_share = min(player["minutes"] / 2600, 1.2)
        log_value = (
            15.6
            + 0.62 * player["_quality"]
            + 0.55 * player["_club_strength"]
            + 0.90 * age_curve
            + positional
            + 0.55 * minutes_share
            + np.log(LEAGUE_PREMIUM[player["league"]])
            + rng.normal(0, 0.42)
        )
        rows.append(
            {
                "source_id": player["source_id"],
                "name": player["name"],
                "league": player["league"],
                "season": player["season"],
                "market_value_eur": float(np.exp(log_value)),
            }
        )
    return rows
