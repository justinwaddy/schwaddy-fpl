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

The last two columns are the only ones the API cannot answer: how far a
price has moved in the last day and in the last week. They come from
pricehist, which keeps the daily snapshots the API does not. New columns
go on the end, never in the middle, because the site indexes these rows
by position.

data/prices.json layout:
    {"generated", "gw", "cols": [...],
     "hist": {"d1", "d7"},   # days each move column actually covers
     "players": {player code: [price, change since season start,
                               selected by %, season points, minutes,
                               form, status, move over the last day,
                               move over the last week]}}
"""
import json
from datetime import datetime, timezone

from . import pricehist

COLS = ["price", "chg", "sel", "pts", "min", "form", "status", "chg1", "chg7"]


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def now_prices(bootstrap):
    return {str(e["code"]): round(e["now_cost"] / 10, 1) for e in bootstrap["elements"]}


def build(bootstrap, deltas=None, spans=None):
    ev = bootstrap.get("events") or []
    gw = next((e["id"] for e in ev if e.get("is_next")), None)
    deltas = deltas or {}
    players = {}
    for e in bootstrap["elements"]:
        code = str(e["code"])
        d1, d7 = deltas.get(code) or (None, None)
        players[code] = [
            round(e["now_cost"] / 10, 1),
            round(_f(e.get("cost_change_start")) / 10, 1),
            _f(e.get("selected_by_percent")),
            int(e.get("total_points") or 0),
            int(e.get("minutes") or 0),
            _f(e.get("form")),
            e.get("status", "a"),
            d1,
            d7,
        ]
    return dict(generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                gw=gw, cols=COLS, hist=spans or {"d1": None, "d7": None},
                players=players)


def write(data_dir, bootstrap=None):
    if bootstrap is None:
        from . import api
        bootstrap = api.classic_bootstrap()
    # The history is the one cumulative file under data/. A corrupt or
    # unwritable one must not cost us prices.json as well, which the whole
    # dashboard leans on; the move columns simply come back empty.
    try:
        deltas, spans = pricehist.update(data_dir, now_prices(bootstrap))
    except Exception as ex:
        print(f"prices: price history unavailable ({ex}); move columns left blank")
        deltas, spans = {}, None
    out = build(bootstrap, deltas, spans)
    json.dump(out, open(f"{data_dir}/prices.json", "w"), separators=(",", ":"))
    return out
