"""Historical injury and availability data, harvested from git history.

The problem. FPL publishes a player's `status`, `chance_of_playing` and a
line of `news` - "Knee injury - Unknown return date" - but only ever for
right now. The archive this repo trains on carries no history of them at
all, so until now the network could not learn what an injury does, and
the live driver had to bolt the flags on afterwards as a multiplier.

The source. vaastav/Fantasy-Premier-League commits `players_raw.csv` once
a gameweek, and that file DOES carry the four fields. Its git history is
therefore a point-in-time injury feed going back to 2016/17 - about forty
snapshots a season, one per gameweek.

    python -m evo.injuries --repo /path/to/Fantasy-Premier-League

writes data/injuries_{season}.csv, a change log rather than a panel: a
row only when a player's state actually changes, which is what keeps five
seasons of six hundred players under a megabyte.

The timing rule, which is the whole reason this is safe. Each row carries
two timestamps:

  observed_at  when the snapshot was taken (the commit)
  start_at     when the state began

`start_at` is FPL's own `news_added` - the moment the item was published
- and it is usually days before the snapshot that first recorded it. A
news item posted at 09:30 on Friday was visible to every manager in the
game from 09:30 on Friday, so dating the state from there is a fact about
what was knowable, not a peek at the future. It is what turns a weekly
snapshot into something with daily resolution.

What is NOT sound is the reverse inference. A snapshot taken after time t
showing a player fit says nothing about whether he was fit at t, so a
state is only ever read forward from its own start, never backward from a
later observation. `features.injury_state` applies exactly that rule, and
selftest.test_non_anticipation truncates this log along with the match
archive so that any violation of it fails a test.

Two honest gaps:

  - The snapshot cadence is a gameweek, so a knock picked up and cleared
    inside one week can be missed entirely - the log records what FPL had
    posted when the collector ran, plus whatever news_added back-dates.
  - Early-season coverage depends on when that season's collector started
    committing; `--report` prints the first snapshot date per season so
    the gap is visible rather than assumed.
"""
import argparse
import csv
import io
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np

from .config import SEASONS, LIVE_SEASON

FIELDS = ("code", "observed_at", "start_at", "status", "chance_this",
          "chance_next", "news")
# FPL's status codes. 'a' available, 'd' doubtful, 'i' injured,
# 's' suspended, 'u' unavailable (left the club, or on loan), 'n' on
# loan / not in the squad.
STATUS_FACTOR = {"a": 1.0, "d": 0.90, "i": 0.65, "s": 0.65, "u": 0.02,
                 "n": 0.30}
OUT_STATUS = ("i", "s", "u", "n")


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True,
                          capture_output=True, text=True).stdout


def _ts(s):
    """ISO 8601 to epoch seconds; None on anything unparseable."""
    if not s or str(s).lower() in ("nan", "none", ""):
        return None
    t = str(s).strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        try:
            d = datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else f


def _snapshot(repo, sha, path):
    """The four fields, by player code, out of one historical blob."""
    try:
        blob = _git(repo, "show", f"{sha}:{path}")
    except subprocess.CalledProcessError:
        return None
    rows = {}
    for r in csv.DictReader(io.StringIO(blob)):
        code = r.get("code")
        if not code:
            continue
        try:
            code = int(float(code))
        except ValueError:
            continue
        rows[code] = dict(
            status=(r.get("status") or "a").strip() or "a",
            chance_this=_num(r.get("chance_of_playing_this_round")),
            chance_next=_num(r.get("chance_of_playing_next_round")),
            news=(r.get("news") or "").strip(),
            news_added=_ts(r.get("news_added")))
    return rows or None


def harvest_season(repo, season, out_dir, verbose=True):
    """Walk every commit that touched this season's players_raw.csv,
    oldest first, and write the change log."""
    path = f"data/{season}/players_raw.csv"
    log = _git(repo, "log", "--format=%H %cI", "--", path).strip().splitlines()
    if not log:
        if verbose:
            print(f"  {season}: no history for {path}")
        return None
    commits = [ln.split(" ", 1) for ln in reversed(log)]   # oldest first

    state, prev_start, out = {}, {}, []
    for sha, when in commits:
        obs = _ts(when)
        snap = _snapshot(repo, sha, path)
        if snap is None:
            continue
        for code, s in snap.items():
            key = (s["status"], s["chance_next"], s["news"])
            if state.get(code) == key:
                continue
            state[code] = key
            # Date the state from FPL's own publication time, but ONLY
            # when this row carries a news item of its own. When the news
            # field is empty the player has been CLEARED, and news_added
            # still points at the injury that has just ended - back-dating
            # to it would say he was fit from the day he got hurt. A
            # clearing is only knowable when the snapshot shows it.
            start = s["news_added"]
            fresh = (s["news"] and start is not None and start <= obs
                     and obs - start <= 400 * 86400)
            if not fresh:
                start = obs
            # states are strictly ordered in time for a given player
            p = prev_start.get(code)
            if p is not None and start <= p:
                start = obs if obs > p else p + 1.0
            prev_start[code] = start
            out.append(dict(code=code, observed_at=round(obs),
                            start_at=round(start), status=s["status"],
                            chance_this=s["chance_this"],
                            chance_next=s["chance_next"], news=s["news"]))
    out.sort(key=lambda r: (r["code"], r["start_at"]))
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, f"injuries_{season}.csv")
    with open(dest, "w", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS)
        w.writeheader()
        for r in out:
            w.writerow(r)
    if verbose:
        first = datetime.fromtimestamp(_ts(commits[0][1]), timezone.utc)
        last = datetime.fromtimestamp(_ts(commits[-1][1]), timezone.utc)
        nout = sum(1 for r in out if r["status"] in OUT_STATUS)
        print(f"  {season}: {len(commits)} snapshots "
              f"{first:%Y-%m-%d} to {last:%Y-%m-%d}, "
              f"{len(out)} state changes ({nout} to an out status), "
              f"{len({r['code'] for r in out})} players -> {dest}")
    return dest


def append_bootstrap(data_dir, boot, now=None, season=LIVE_SEASON,
                     verbose=True):
    """Append the live game's current state to this season's change log.

    The harvest above stops wherever the archive repo last committed, which
    during a live season is usually days behind. The draft API carries the
    same four fields for right now, and this repo already pulls it daily,
    so one call a day keeps the log current - and next season the whole
    thing is already harvested rather than needing to be reconstructed.

    Rows are appended only where the state has actually changed, so
    running it twice in a day is a no-op.
    """
    now = now or datetime.now(timezone.utc).timestamp()
    path = os.path.join(data_dir, f"injuries_{season}.csv")
    existing = list(csv.DictReader(open(path))) if os.path.exists(path) else []
    last = {}
    for r in existing:
        last[int(r["code"])] = (r["status"], _num(r["chance_next"]),
                                r["news"])
    prev_start = {}
    for r in existing:
        prev_start[int(r["code"])] = float(r["start_at"])

    add = []
    for e in boot.get("elements", []):
        code = int(e["code"])
        st = (e.get("status") or "a").strip() or "a"
        ch = _num(e.get("chance_of_playing_next_round"))
        news = (e.get("news") or "").strip()
        if last.get(code) == (st, ch, news):
            continue
        start = _ts(e.get("news_added"))
        if not (news and start is not None and start <= now
                and now - start <= 400 * 86400):
            start = now
        p = prev_start.get(code)
        if p is not None and start <= p:
            start = now if now > p else p + 1.0
        add.append(dict(code=code, observed_at=round(now),
                        start_at=round(start), status=st,
                        chance_this=_num(e.get("chance_of_playing_this_round")),
                        chance_next=ch, news=news))
    if not add:
        if verbose:
            print(f"  injuries: no change since the last snapshot ({path})")
        return 0
    rows = existing + [{k: ("" if v is None else v) for k, v in r.items()}
                       for r in add]
    rows.sort(key=lambda r: (int(r["code"]), float(r["start_at"])))
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    if verbose:
        print(f"  injuries: {len(add)} state changes appended to {path}")
    return len(add)


def last_observation(data_dir, season):
    """Epoch seconds of the most recent snapshot in the log, or None."""
    path = os.path.join(data_dir, f"injuries_{season}.csv")
    if not os.path.exists(path):
        return None
    t = [float(r["observed_at"]) for r in csv.DictReader(open(path))]
    return max(t) if t else None


MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
     "nov", "dec"))}
_BACK = re.compile(r"(?:expected back|suspended until|back)\s+(\d{1,2})\s+([A-Za-z]{3})",
                   re.I)
# how long a state lasts when the news gives no date: a knock is this
# round's question, an injury without a date is a long one, a suspension
# is a match or three, a loan is the season
DEFAULT_DAYS = {"d": 4.0, "i": 42.0, "s": 10.0, "n": 60.0, "u": 400.0}


def expected_return(status, news, start):
    """(return_at epoch, known) from the news line, as at its start.

    "Calf injury - Expected back 13 Sep" is FPL's own estimate, published
    with the item, and it is the one thing that separates a player worth
    carrying on the bench from one worth dropping. The year is the one
    that puts the date after the item was posted.
    """
    if status == "a":
        return float("nan"), 0.0
    m = _BACK.search(news or "")
    if m and m.group(2).lower() in MONTHS:
        d, mo = int(m.group(1)), MONTHS[m.group(2).lower()]
        y = datetime.fromtimestamp(start, timezone.utc).year
        for yy in (y, y + 1):
            try:
                cand = datetime(yy, mo, d, 12, tzinfo=timezone.utc).timestamp()
            except ValueError:
                continue
            if cand >= start - 7 * 86400:
                return cand, 1.0
    return start + DEFAULT_DAYS.get(status, 42.0) * 86400.0, 0.0


def load(data_dir, season):
    """{code: array of (start, factor, out, doubt, observed, return_at,
    return_known)} sorted by start.

    factor is the availability the game itself was advertising: the
    published chance of playing where there is one, and the status
    default where there is not. return_at is when he is expected back.
    """
    path = os.path.join(data_dir, f"injuries_{season}.csv")
    if not os.path.exists(path):
        return {}
    by = {}
    for r in csv.DictReader(open(path)):
        code = int(r["code"])
        st = r["status"] or "a"
        ch = _num(r["chance_next"])
        f = STATUS_FACTOR.get(st, 1.0)
        if ch is not None and st in ("d", "i", "s"):
            f = ch / 100.0
        start = float(r["start_at"])
        ret, known = expected_return(st, r.get("news", ""), start)
        by.setdefault(code, []).append(
            (start, f, 1.0 if st in OUT_STATUS else 0.0,
             1.0 if st == "d" else 0.0, float(r["observed_at"]), ret, known))
    out = {}
    for code, rows in by.items():
        rows.sort()
        a = np.array(rows, float)
        out[code] = a
    return out


FIT = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, float("nan"), 0.0)


def state_at(rows, t):
    """(factor, out, doubt, days_in_state, days_stale, known, return_at,
    return_known) at time t.

    Only intervals that had already STARTED by t are eligible: a state is
    read forward from its own start and never backward from a later
    observation.
    """
    if rows is None or len(rows) == 0:
        return FIT
    k = int(np.searchsorted(rows[:, 0], t, "right")) - 1
    if k < 0:
        return FIT
    start, f, out, doubt, obs, ret, known = rows[k]
    return (float(f), float(out), float(doubt), (t - start) / 86400.0,
            (t - min(obs, t)) / 86400.0, 1.0, float(ret), float(known))


def factor_at(state, t_fixture, t_now):
    """The availability multiplier for a fixture at t_fixture, given the
    state as at t_now. This round is whatever the game advertises; a
    later round is fit once the expected return has passed."""
    f, out, doubt, _, _, known, ret, _ = state
    if t_fixture <= t_now + 6 * 86400 or f >= 1.0:
        return f
    if not np.isnan(ret) and t_fixture >= ret:
        return 1.0
    return f


def main(argv=None):
    ap = argparse.ArgumentParser(prog="evo.injuries")
    ap.add_argument("--repo", default=None,
                    help="a clone of vaastav/Fantasy-Premier-League with "
                         "history (git fetch --filter=blob:none --depth=4000)")
    ap.add_argument("--out", default="data")
    ap.add_argument("--seasons", nargs="+",
                    default=list(SEASONS) + [LIVE_SEASON])
    ap.add_argument("--live", action="store_true",
                    help="skip the harvest and just append the live game's "
                         "current state from the draft API")
    a = ap.parse_args(argv)
    if a.live:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from schwaddy import api
        append_bootstrap(a.out, api.draft_bootstrap())
        return 0
    print(f"harvesting injury history from {a.repo}")
    for s in a.seasons:
        harvest_season(a.repo, s, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
