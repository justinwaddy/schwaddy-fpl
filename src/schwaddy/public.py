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
import re

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


# Every kind of event in the pipeline's feed travels, because almost all of
# it is plain fact about the league: who claimed whom, who is injured, who
# hauled, how the table moved, and the editorial lane's football news.
#
# The exception is the research into Justin's own starting eleven, which is
# the one thing here that is genuinely his edge - whether Garner starts,
# whether Marmoush is in the XI, what the model projects. Those three kinds
# of event carry a scope, and news.py sets it to "mine" exactly when the
# item is about his own squad, so that is the line: a headline, a squad
# note or a projection scoped to him stays behind, and the same kinds
# scoped to the league or to a free agent travel like everything else.
#
# The scope test is applied only to those three. It is set loosely
# elsewhere - the GW scoreboards are "mine" too - so using it as a general
# filter would drop half the league's results.
PERSONAL_TYPES = ("headline", "squad", "projection")
NEWS_KEEP = 400   # the whole feed comfortably; news.py trims its own file
# Belt and braces over the scope rule: a sentence that talks about the
# forecast does not travel, whatever event it is filed under. The editorial
# lane often closes a piece of ordinary football news with a line on what
# the model makes of the player - "he is NOT in the draft pool yet ... when
# he lands the model will underrate him for four gameweeks". Dropping the
# whole item over that loses real news, so only the offending sentence is
# cut, and the item goes only if nothing is left standing.
NEWS_BANNED_TEXT = ("project", "expected pts", "the model", "model will",
                    "your dashboard", "next 5", "next5", "availability")


def _load(path):
    return json.load(open(path)) if os.path.exists(path) else None


def _trim(text):
    """The text with any sentence about the forecast taken out."""
    text = (text or "").strip()
    keep = [s for s in re.split(r"(?<=[.!?])\s+", text)
            if not any(b in s.lower() for b in NEWS_BANNED_TEXT)]
    out = " ".join(keep).strip()
    # a fragment left over from heavy cutting says nothing; drop it
    return out if len(out) >= 25 else ""


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

    teams = dict(stats.get("teams") or {})
    gw = league.get("gw")

    # When line-ups lock. The pages compute the countdown themselves from
    # this, rather than anybody writing "closes today" into a file that is
    # wrong by tomorrow. waivers_time is the same gameweek's waiver
    # processing, which is the other clock a manager cares about.
    boot = _load(f"{data_dir}/draft_bootstrap.json") or {}
    # the club's own code, which is what the league's badge URLs are keyed
    # on: resources.premierleague.com/premierleague/badges/70/t<code>.png
    for t in boot.get("teams") or []:
        row = teams.get(str(t.get("id")))
        if row and len(row) < 3:
            teams[str(t["id"])] = [row[0], row[1], t.get("code")]
    ev = boot.get("events") or {}
    nxt_ev = next((e for e in (ev.get("data") or [])
                   if e.get("id") == ev.get("next")), None)
    deadline = (nxt_ev or {}).get("deadline_time")
    waivers = (nxt_ev or {}).get("waivers_time")
    next_gw = (nxt_ev or {}).get("id")
    nxt = _next_fixtures(fixtures, (gw or 0) + 1)
    # The coming gameweek's fixture list. The live feed reports whichever
    # gameweek the game itself considers current, which between a Monday
    # and the next deadline is the one that has just finished, so without
    # this the Live tab spends half the week showing last week's results.
    nxt_fx = sorted(([f.get("team_h"), f.get("team_a"), f.get("kickoff_time")]
                     for f in fixtures if f.get("event") == next_gw),
                    key=lambda r: r[2] or "")

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
        kind = e.get("type") or ""
        if kind in PERSONAL_TYPES and e.get("scope") == "mine":
            continue                     # his own starters, and the model
        text = _trim(e.get("text") or "")
        # news.py addresses Justin directly in a few places, and not only
        # in the three personal kinds: "Your GW2 best: Haaland 13, ..." is
        # filed as a score. Anything written to him is written for him.
        if not text or text.lower().startswith("your "):
            continue
        news.append(dict(ts=e.get("ts"), type=kind, text=text[:400],
                         url=(e.get("url") or "")[:300] or None))
    news.sort(key=lambda e: e.get("ts") or "", reverse=True)

    return dict(
        generated=stats.get("generated") or league.get("generated"),
        season=stats.get("season"), gw=gw,
        next_gw=next_gw, deadline=deadline, waivers=waivers,
        finished=league.get("finished"), all_played=league.get("all_played"),
        teams=teams, managers=managers, players=players,
        fixtures=nxt_fx, news=news[:NEWS_KEEP])


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
