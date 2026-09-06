"""Turn a Transfermarkt-derived CSV into data/market_values.json.

Run once, then again whenever you refresh the snapshot (monthly is plenty —
market valuations move slowly and are only re-estimated a few times a year).

    python tools/import_market_values.py ~/Downloads/players.csv --season 2025

The Kaggle datasets change their column names occasionally, so every column is
overridable rather than hardcoded:

    python tools/import_market_values.py players.csv --season 2025 \
        --name-col name --value-col market_value_in_eur \
        --league-col current_club_domestic_competition_id

Nothing about the raw dataset is committed. Only the four fields the join needs
are written out, which keeps this repository from redistributing someone else's
data wholesale.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "market_values.json"

# Transfermarkt competition ids as they appear in the common Kaggle exports.
# Anything not listed is dropped, which is how the other 150 leagues in the file
# get filtered out without a separate step.
COMPETITION_TO_LEAGUE = {
    "GB1": "EPL",
    "ES1": "LALIGA",
    "IT1": "SERIEA",
    "L1": "BUNDESLIGA",
    "FR1": "LIGUE1",
    # Some exports use full names instead of codes.
    "premier-league": "EPL",
    "laliga": "LALIGA",
    "serie-a": "SERIEA",
    "bundesliga": "BUNDESLIGA",
    "ligue-1": "LIGUE1",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--season", type=int, required=True,
                        help="season these valuations belong to, matching config/leagues.yaml")
    parser.add_argument("--name-col", default="name")
    parser.add_argument("--value-col", default="market_value_in_eur")
    parser.add_argument("--league-col", default="current_club_domestic_competition_id")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    if not args.csv_path.exists():
        print(f"no such file: {args.csv_path}", file=sys.stderr)
        return 1

    rows, skipped = [], Counter()
    with args.csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = {args.name_col, args.value_col, args.league_col} - set(reader.fieldnames or [])
        if missing:
            print(f"columns not in this CSV: {', '.join(sorted(missing))}", file=sys.stderr)
            print(f"available: {', '.join(reader.fieldnames or [])}", file=sys.stderr)
            return 1

        for record in reader:
            league = COMPETITION_TO_LEAGUE.get((record.get(args.league_col) or "").strip())
            if not league:
                skipped["outside the big five"] += 1
                continue
            raw = (record.get(args.value_col) or "").strip()
            if not raw:
                skipped["no valuation"] += 1
                continue
            try:
                value = float(raw)
            except ValueError:
                skipped["unparseable value"] += 1
                continue
            if value <= 0:
                skipped["no valuation"] += 1
                continue
            name = (record.get(args.name_col) or "").strip()
            if not name:
                skipped["no name"] += 1
                continue
            rows.append({
                "league": league,
                "name": name,
                "season": args.season,
                "market_value_eur": value,
            })

    if not rows:
        print("nothing matched — check --league-col against the codes in your file", file=sys.stderr)
        return 1

    # Keep the highest valuation per player per league: exports often carry one
    # row per valuation date rather than one per player.
    best: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["league"], row["name"].lower())
        if key not in best or row["market_value_eur"] > best[key]["market_value_eur"]:
            best[key] = row
    final = sorted(best.values(), key=lambda row: (row["league"], row["name"]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(final, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                        encoding="utf-8")

    per_league = Counter(row["league"] for row in final)
    print(f"wrote {len(final)} players to {args.out}")
    for league, count in sorted(per_league.items()):
        print(f"  {league}: {count}")
    for reason, count in skipped.most_common():
        print(f"  skipped {count} rows: {reason}")
    if min(per_league.values()) < 200:
        print("\nwarning: a league came out thin. Check the season and the league column.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
