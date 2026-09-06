"""Build a single self-contained HTML file.

The deployed site fetches its data as separate files. This bundles the whole
thing — markup, styles, script and data — into one HTML document that works from
a file:// URL with no server, which is handy for sending someone the project
without asking them to clone and run anything.

    python tools/bundle_preview.py [--out preview.html] [--min-minutes 1200]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = WEB / "data"


def build(out: Path, min_minutes: int) -> Path:
    payload = {name: json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
               for name in ("meta", "ledger", "fixtures", "model_match", "model_value", "analytics")}

    players = json.loads((DATA / "players.json").read_text(encoding="utf-8"))
    columns = players["columns"]
    minutes_at = columns.index("minutes")
    id_at = columns.index("player_id")

    # A single file has to stay openable, so the preview keeps regular starters
    # and drops deep squad players. The deployed site carries everyone.
    kept = [row for row in players["rows"] if (row[minutes_at] or 0) >= min_minutes]
    kept_ids = {row[id_at] for row in kept}
    payload["players"] = {"columns": columns, "rows": kept}

    similar = {}
    for shard in sorted((DATA / "similar").glob("*.json")):
        league = shard.stem
        source = json.loads(shard.read_text(encoding="utf-8"))
        similar[league] = {
            player_id: [pair for pair in pairs if pair[0] in kept_ids][:8]
            for player_id, pairs in source.items()
            if int(player_id) in kept_ids
        }
    payload["similar"] = similar

    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")

    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace('<link rel="stylesheet" href="styles.css">', f"<style>\n{css}\n</style>")
    html = html.replace(
        '<script src="app.js" defer></script>',
        f'<script>window.__BIGFIVE__ = {blob};</script>\n<script>\n{js}\n</script>',
    )
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "preview.html"))
    parser.add_argument("--min-minutes", type=int, default=1200)
    args = parser.parse_args()
    path = build(Path(args.out), args.min_minutes)
    print(f"{path} — {path.stat().st_size / 1024:.0f} KB")
