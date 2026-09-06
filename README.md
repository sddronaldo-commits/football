# Big Five Lab

A football model that publishes its record instead of claiming one.

Every week a scheduled job pulls results from the five largest European leagues,
rebuilds its features, retrains, predicts the coming round and commits those
predictions to this repository. After the matches are played, a second job scores
them and commits the results. Nothing is computed when you load the site — the
whole thing is static files produced in CI.

The point is not the accuracy number. It is that the accuracy number is checkable:

```bash
git log --follow data/ledger/predictions.jsonl   # predictions, dated before kickoff
git log --follow data/ledger/results.jsonl       # scores, dated after
```

A prediction can never be edited. `ledger.record()` refuses to write a match id
that already exists, and there is a test that proves it.

---

## Current record

Filled in by the pipeline; the site renders it from `web/data/ledger.json`.

| | |
|---|---|
| Leagues | Premier League, La Liga, Serie A, Bundesliga, Ligue 1 |
| Match model | pooled gradient boosting over all five leagues, league as a feature |
| Accuracy | ~53%, against ~47% for always picking the home side |
| Draw recall | close to zero, and shown on the site rather than hidden |
| Value model | ridge on log market value, coefficients shipped to the browser |
| Style model | cosine nearest neighbours on eight per-90 measures, z-scored within league |

## Why five leagues and not one

Three things fall out of pooling that a Premier-League-only version cannot do.

**More data.** One league offers about 340 usable matches a season after form
features need a warm-up. Five offer roughly 1,700, which is the difference
between a classifier that fits noise and one that generalises.

**The league premium.** Fitting one value model across all five, with league
indicators, puts a number on what the market pays for identical output in each
country. England carries roughly a 2× premium over France once age, minutes and
production are held equal. That is a finding, not a chart.

**Cross-league scouting.** "Find a Ligue 1 player who profiles like this Serie A
forward" is the question clubs actually ask. Same-league similarity is a toy.

## Architecture

```
football-data.org ─┐
Understat ─────────┼→ ingest → crosswalk → features ─┬→ match model ──┐
Transfermarkt ─────┘                                 ├→ value model ──┼→ static JSON → site
                                                     └→ style model ──┘
```

Two tables carry everything downstream:

- `team_match` — one row per team per match, with rolling form shifted by one
  match so a fixture never sees its own result
- `player_season` — one row per player-season, per-90 rates plus z-scores
  computed **within each league-season**, because raw per-90 numbers are not
  comparable across leagues

The models are deliberately modest. The engineering around them is the point.

## Running it

```bash
make install
make demo        # full pipeline on synthetic data, no token, no network
make serve       # http://localhost:8000
make test
```

`make demo` generates a complete synthetic dataset — real club names, invented
players, simulated results — so a fresh clone works with no credentials. Every
exported file is stamped `"mode": "demo"` and the site shows a badge. Synthetic
numbers must never be able to pass as real ones.

### Going live without a local checkout

Everything can run from the Actions tab:

1. Add `FOOTBALL_DATA_TOKEN` as a repository secret
2. Enable Pages under Settings → Pages, source "GitHub Actions"
3. Drag your Transfermarkt CSV into the repo through the web UI
4. Actions → **Set up live data** → Run workflow

That workflow verifies the token against the API, converts the CSV, clears the
seeded demo ledger, runs the pipeline live, publishes the site, and writes the
market-value join rate plus every unmatched name into the run summary. Delete the
CSV afterwards; only the four fields the join needs are kept.

The predict and score workflows call the Pages deploy directly as a second job
rather than relying on their own commit to trigger it, because a push made with
the default `GITHUB_TOKEN` does not start other workflows.

### Going live from a terminal

```bash
export FOOTBALL_DATA_TOKEN=...                              # football-data.org/client/register
python tools/import_market_values.py players.csv --season 2025
rm data/ledger/*.jsonl                                      # demo predictions are not your record
make run
```

The pipeline switches to live mode purely on the presence of the token; there is
no flag. Clearing the ledger first matters — the seeded demo rows are marked
`mode: demo`, but leaving them in would mean the headline number on the site
mixes simulated predictions with real ones.

## Deploying

The site is static files in `web/` with no build step, so it deploys as-is:

- **Vercel** — import the repo, framework preset "Other". `vercel.json` sets the
  output directory and cache headers. Each pipeline commit triggers a redeploy.
- **GitHub Pages** — `.github/workflows/pages.yml` publishes `web/` on push.

Scheduled workflows on public repositories are disabled after roughly 60 days
without repository activity, and pushes made with the default `GITHUB_TOKEN` may
not reset that clock. Set a `PIPELINE_TOKEN` secret with a personal access token
if you want the cron to survive unattended.

## What this can't do

Written down here because a portfolio project that hides its weaknesses is worth
less than one that names them.

- **Market values are not transfer fees.** They are crowd estimates, driven
  heavily by age, contract length and club prestige. The value model largely
  learns "young, plays a lot, plays for a big club." Read its residuals as
  disagreement with the crowd, not as discovered truth.
- **The match model will not beat a bookmaker.** Around 53% is where this class
  of model lands. Draws are the reason: a draw is rarely the single most likely
  outcome even though it is common, so the model almost never picks one. The
  site shows that failure explicitly.
- **The style map cannot be validated.** There is no ground truth for "similar
  player", so it is presented as a visualisation, not a model with a score.
- **This is not a betting tool.** No odds are ingested and none are compared.
- **Demo data is fake.** If the badge says synthetic, the numbers are simulated.

## Data sources

| Source | Used for | Terms |
|---|---|---|
| [football-data.org](https://www.football-data.org/) | fixtures, results, tables | free tier: 12 competitions, 10 requests/minute, delayed scores, no player statistics |
| [Understat](https://understat.com/) | player statistics and expected goals | scraped politely, cached, one request per league-season per week |
| Transfermarkt-derived dataset | market values | derived values only; the raw dataset is not redistributed here |

Results and values belong to their sources. This repository publishes derived
numbers and its own predictions.

## Layout

```
config/leagues.yaml     every league, season and threshold — adding a league is config
pipeline/               ingest, crosswalk, features, models, ledger, export
data/ledger/            append-only predictions and results (the audit trail)
web/                    the site: three static files plus generated JSON
tools/bundle_preview.py builds a single self-contained HTML file
tests/                  the failure modes that actually matter
```

## Tests worth reading

`tests/test_pipeline.py` is short and each test maps to a real failure:

- form features must not contain the current match's result (label leak)
- Ligue 1 and the Bundesliga must produce 34 matchdays, not 38
- per-90 z-scores must be comparable across leagues
- the market-value join rate must stay above 95%, so a source changing its name
  formatting fails CI instead of quietly halving the dataset
- the ledger must refuse to overwrite a recorded prediction
- accuracy above 75% fails the suite, because that means a leak, not a model
