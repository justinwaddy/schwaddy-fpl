"""Classic-game prices for the draft dashboard: data/prices.json.

The draft game has no prices, but the classic game's price is an
independent read on a player that the model does not have: the market's
prior on his quality, moved nightly by what a million managers do. Beside
the model's next5 it separates "the model has not seen him yet" (a big
signing priced at 7.5 with next5 near zero) from "he is not playing"
(a 4.5 reserve with the same next5).

Written on every refresh, news-only runs included, because prices change
overnight and the waiver deadline falls between the morning runs. Keyed
by player code, matching predictions.json, and stored as bare arrays with
a `cols` header because the site fetches it on every load.

data/prices.json layout:
    {"generated", "gw", "cols": [...],
     "players": {player code: [price, change since season start,
                               selected by %, season points, minutes,
                               form, status]}}
"""
import json
from datetime import datetime, timezone

COLS = ["price", "chg", "sel", "pts", "min", "form", "status"]


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def build(bootstrap):
    ev = bootstrap.get("events") or []
    gw = next((e["id"] for e in ev if e.get("is_next")), None)
    players = {}
    for e in bootstrap["elements"]:
        players[str(e["code"])] = [
            round(e["now_cost"] / 10, 1),
            round(_f(e.get("cost_change_start")) / 10, 1),
            _f(e.get("selected_by_percent")),
            int(e.get("total_points") or 0),
            int(e.get("minutes") or 0),
            _f(e.get("form")),
            e.get("status", "a"),
        ]
    return dict(generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                gw=gw, cols=COLS, players=players)


def write(data_dir, bootstrap=None):
    if bootstrap is None:
        from . import api
        bootstrap = api.classic_bootstrap()
    out = build(bootstrap)
    json.dump(out, open(f"{data_dir}/prices.json", "w"), separators=(",", ":"))
    return out
