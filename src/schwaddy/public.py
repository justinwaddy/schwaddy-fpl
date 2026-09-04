"""Model-free view of the league for the per-manager sites: data/public.json.

The six sites under site/<name>/ are for the other managers in the league,
so nothing this engine computes may reach them. That is a data problem,
not a presentation one: a field merely left out of a table is still in the
JSON the page fetched, one devtools tab away. So the public sites read
this file and nothing else, and this file is built by naming the fields
that go in rather than the fields that stay out - a whitelist cannot leak
a covariate somebody adds upstream next month.

Kept, all of it published by the game itself: who owns whom, the live and
settled scores, the league table, each player's season counting stats and
his next fixture.

Dropped: everything in predictions.json (avail, gw, rest, next5, fix5,
run, n_career) and the model columns league.json carries alongside the
real ones (avail, ep_week, ep_next, rest, next5, fix5, run, proj). Also
"mine", since whose squad is whose is the reader's business, not this
file's.

Prices are NOT here. They live in data/prices.json, which only Ed's site
fetches.
"""
import json
import os

# The counting stats the game publishes. Deliberately no expected-goals or
# ICT family: those are somebody else's model, and the point of this file
# is that it carries no model at all.
PUBLIC_STATS = (
    "minutes", "starts", "total_points", "goals_scored", "assists",
    "clean_sheets", "goals_conceded", "saves", "yellow_cards", "red_cards",
    "bonus", "bps", "form", "points_per_game",
)

# Straight off league.json. Anything not named here does not travel.
MGR_KEYS = ("entry", "name", "team", "live", "raw", "subs", "to_play",
            "played", "bench", "rank", "total", "event_total", "gw_rank")
SQUAD_KEYS = ("id", "slot", "name", "pos", "team", "pts", "mins", "played",
              "settled", "to_play", "status", "news", "subbed_in", "subbed_out")


# The pipeline's own feed is mostly public fact - who claimed whom, who is
# injured, who hauled, how the table moved - and that is worth carrying.
# Three kinds are not. "projection" is the model talking. "squad" is
# written from Justin's chair ("Your GW2 still to play"). "headline" is
# the editorial lane, which is research commissioned for one manager.
NEWS_TYPES = ("move", "injury", "recovery", "haul", "flop", "lowlight",
              "score", "live", "wrap", "overtake", "race", "bench", "pint",
              "freeagent")
NEWS_KEEP = 150
# Belt and braces over the type whitelist: an event whose text talks about
# a forecast does not travel, whatever it calls itself.
NEWS_BANNED_TEXT = ("project", "expected pts", "the model", "next 5", "next5")


def _load(path):
    return json.load(open(path)) if os.path.exists(path) else None


def _next_fixtures(fixtures, gw):
    """Each club's next opponent, upper case at home, from the fixture list.

    The published schedule, not a forecast: a club with two matches in the
    gameweek shows both, one with none shows a blank.
    """
    out = {}
    for f in sorted(fixtures or [], key=lambda x: x.get("kickoff_time") or ""):
        if f.get("event") != gw:
            continue
        out.setdefault(f["team_h"], []).append(("H", f["team_a"], f.get("kickoff_time")))
        out.setdefault(f["team_a"], []).append(("A", f["team_h"], f.get("kickoff_time")))
    return out


def build(data_dir):
    league = _load(f"{data_dir}/league.json") or {}
    stats = _load(f"{data_dir}/player_stats.json") or {}
    preds = _load(f"{data_dir}/predictions.json") or {}
    fixtures = _load(f"{data_dir}/fixtures_2627.json") or []

    teams = stats.get("teams") or {}
    gw = league.get("gw")
    nxt = _next_fixtures(fixtures, (gw or 0) + 1)

    # ownership is a fact of the draft, not a projection; it comes from
    # predictions.json only because that is where the id->owner map is
    # already assembled by the refresh that just ran
    owner_of = {str(k): v for k, v in (preds.get("owned") or {}).items()}

    def label(team_id):
        """"BOU" at home, "bou" away, joined for a double, "-" for a blank."""
        got = nxt.get(team_id) or []
        if not got:
            return "-"
        return "+".join((teams.get(str(o), ["?"])[0].upper() if h == "H"
                         else teams.get(str(o), ["?"])[0].lower())
                        for h, o, _ in got)

    # league.json holds the squads as they were for the gameweek it
    # scored. Waivers process a day before the next deadline, so between
    # the two that squad is out of date and a manager looking at his own
    # page would see players he no longer has. Ownership from the draft
    # API is current, so each manager also carries his roster as it stands
    # now, and the site leads with that.
    roster_of = {}
    for p in stats.get("players", {}).values():
        owner = owner_of.get(str(p.get("id")))
        if owner is None:
            continue
        roster_of.setdefault(owner, []).append(dict(
            code=str(_code_of(p, stats)), id=p.get("id"), name=p.get("name"),
            pos=p.get("pos"), team=p.get("team"), status=p.get("status"),
            news=p.get("news"), next=label(p.get("team"))))
    order = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
    for v in roster_of.values():
        v.sort(key=lambda x: (order.get(x["pos"], 9), x["name"] or ""))

    managers = []
    for m in league.get("managers") or []:
        squad = [{k: p.get(k) for k in SQUAD_KEYS} for p in m.get("squad") or []]
        for p in squad:
            p["next"] = label(_team_id_of(p.get("team"), teams))
        row = {k: m.get(k) for k in MGR_KEYS}
        row["squad"] = squad
        row["roster"] = roster_of.get(m.get("entry"), [])
        managers.append(row)

    players = []
    for code, p in (stats.get("players") or {}).items():
        s = p.get("s") or {}
        players.append(dict(
            code=str(code), id=p.get("id"), name=p.get("name"),
            full=p.get("full"), pos=p.get("pos"), team=p.get("team"),
            status=p.get("status"), news=p.get("news"),
            owner=owner_of.get(str(p.get("id"))),
            next=label(p.get("team")),
            s={k: s[k] for k in PUBLIC_STATS if k in s}))
    players.sort(key=lambda p: (-(p["s"].get("total_points") or 0), p["name"] or ""))

    news = []
    for e in (_load(f"{data_dir}/news.json") or {}).get("events") or []:
        if e.get("type") not in NEWS_TYPES:
            continue
        text = (e.get("text") or "").strip()
        low = text.lower()
        if not text or any(b in low for b in NEWS_BANNED_TEXT):
            continue
        news.append(dict(ts=e.get("ts"), type=e.get("type"), text=text[:400]))
    news.sort(key=lambda e: e.get("ts") or "", reverse=True)

    return dict(
        generated=stats.get("generated") or league.get("generated"),
        season=stats.get("season"), gw=gw,
        finished=league.get("finished"), all_played=league.get("all_played"),
        teams=teams, managers=managers, players=players,
        news=news[:NEWS_KEEP])


def _code_of(p, stats):
    """player_stats is keyed by code, so recover it from the entry."""
    for code, q in (stats.get("players") or {}).items():
        if q is p:
            return code
    return p.get("id")


def _team_id_of(short, teams):
    """league.json squads carry the club as a short name, not an id."""
    for tid, names in (teams or {}).items():
        if names and names[0] == short:
            return int(tid)
    return None


def write(data_dir):
    out = build(data_dir)
    json.dump(out, open(f"{data_dir}/public.json", "w"), separators=(",", ":"))
    return out
