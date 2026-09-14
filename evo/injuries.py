"""Historical injury and availability data, harvested from git history.

The problem. FPL publishes a player's `status`, `chance_of_playing` and a
line of `news` - "Knee injury - Unknown return date" - but only ever for
right now. The archive this repo trains on carries no history of them at
all, so until now the network could not learn what an injury does, and
the live driver had to bolt the flags on afterwards as a multiplier.

The sources. vaastav/Fantasy-Premier-League commits `players_raw.csv`
once a gameweek, and that file DOES carry the four fields. Its git
history is therefore a point-in-time injury feed going back to 2016/17 -
about forty snapshots a season, one per gameweek - except in 2025/26,
where it committed twelve times and not at all from 1 Nov to 5 Feb or
from 13 Mar to 17 Jun. Two more sources carry the same fields and fill
that: olbauday/FPL-Elo-Insights commits `playerstats.csv` (status, both
chances, news, news_added, keyed by element id) once or twice a day, and
the Wayback Machine holds near-daily captures of FPL's own
bootstrap-static. Every source yields snapshots of the same shape, they
are merged in time order, and the change log is written off the merge.

    python -m evo.injuries --repo /path/to/Fantasy-Premier-League \
        [--elo-repo /path/to/FPL-Elo-Insights] \
        [--wayback 20251101 20260205] [--seasons 2025-26]
    python -m evo.injuries --report      # snapshots and worst gap, per season

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

  - The snapshot cadence is a gameweek in the seasons that have only the
    archive repo, so a knock picked up and cleared inside one week can be
    missed entirely - the log records what FPL had posted when the
    collector ran, plus whatever news_added back-dates. 2025/26 onward is
    daily.
  - Early-season coverage depends on when that season's collector started
    committing; `--report` prints the first snapshot date per season so
    the gap is visible rather than assumed.

Staleness. The distinct observed_at values in a log are the times the
feed was read (every read moves somebody), and `snapshot_times` returns
them so that `state_at` can report how long since the feed last CONFIRMED
a state rather than how long since the state began. Through a hole in the
feed that number grows and the inj_stale feature saturates, which is what
the network should see instead of a confident but ancient status;
selftest.test_injury_coverage fails on a hole longer than it should be.
"""
import argparse
import csv
import io
import json
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


def _state(status, chance_this, chance_next, news, news_added):
    """One player's state in one snapshot, the same tuple from every
    source: (status, chance_this, chance_next, news, news_added)."""
    return ((status or "a").strip() or "a", _num(chance_this),
            _num(chance_next), (news or "").strip(), _ts(news_added))


def _git_commits(repo, path):
    """(sha, iso time) of every commit touching path, oldest first."""
    log = _git(repo, "log", "--format=%H %cI", "--", path).strip().splitlines()
    return [tuple(ln.split(" ", 1)) for ln in reversed(log)]


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
        rows[code] = _state(r.get("status"),
                            r.get("chance_of_playing_this_round"),
                            r.get("chance_of_playing_next_round"),
                            r.get("news"), r.get("news_added"))
    return rows or None


# ------------------------------------------------------------- sources
# Each yields (observed_at, {code: state}) in time order.

def vaastav_snapshots(repo, season):
    """vaastav/Fantasy-Premier-League: players_raw.csv, about weekly."""
    path = f"data/{season}/players_raw.csv"
    for sha, when in _git_commits(repo, path):
        snap = _snapshot(repo, sha, path)
        if snap:
            yield _ts(when), snap


def elo_season(season):
    """'2025-26' -> '2025-2026', the FPL-Elo-Insights directory name."""
    return f"{season[:4]}-20{season[-2:]}"


def elo_snapshots(repo, season, id2code):
    """olbauday/FPL-Elo-Insights: playerstats.csv, once or twice a day.

    The file is a cumulative panel - one row per player per gameweek -
    keyed by element id, and it is the CURRENT gameweek's rows that are
    rewritten with each commit, so a player's state at the commit is his
    newest gameweek's row. Earlier gameweeks' rows are frozen and are not
    read. news_added is blank in most of its history, so states are
    dated from the commit: daily resolution rather than to the minute.
    """
    path = f"data/{elo_season(season)}/playerstats.csv"
    for sha, when in _git_commits(repo, path):
        try:
            blob = _git(repo, "show", f"{sha}:{path}")
        except subprocess.CalledProcessError:
            continue
        rows, gw_of = {}, {}
        for r in csv.DictReader(io.StringIO(blob)):
            try:
                pid = int(float(r["id"]))
                gw = int(float(r.get("gw") or -1))
            except (KeyError, ValueError):
                continue
            code = id2code.get(pid)
            if code is None or gw < gw_of.get(pid, -2):
                continue
            gw_of[pid] = gw
            rows[code] = _state(r.get("status"),
                                r.get("chance_of_playing_this_round"),
                                r.get("chance_of_playing_next_round"),
                                r.get("news"), r.get("news_added"))
        if rows:
            yield _ts(when), rows


WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
FPL_BOOTSTRAP = "fantasy.premierleague.com/api/bootstrap-static/"


def bootstrap_states(boot):
    """{code: state} out of one bootstrap-static payload."""
    return {int(e["code"]): _state(e.get("status"),
                                   e.get("chance_of_playing_this_round"),
                                   e.get("chance_of_playing_next_round"),
                                   e.get("news"), e.get("news_added"))
            for e in boot.get("elements", []) if e.get("code") is not None}


def wayback_snapshots(start, end, verbose=True):
    """The Wayback Machine's captures of FPL's bootstrap-static, one a
    day, between two YYYYMMDD dates. FPL's own payload, so the fields are
    exactly the live ones; slower and flakier than a git log, which is
    why it is the source for a stretch nothing else covers rather than
    for a whole season."""
    import time
    import requests

    def get(url, **kw):
        for i in range(6):
            try:
                r = requests.get(url, timeout=180, **kw)
                if r.status_code == 200:
                    return r
            except requests.RequestException:
                pass
            time.sleep(5 * (i + 1))
        return None

    r = get(WAYBACK_CDX, params={"url": FPL_BOOTSTRAP, "from": start,
                                 "to": end, "output": "json",
                                 "fl": "timestamp", "filter": "statuscode:200",
                                 "collapse": "timestamp:8"})
    if r is None:
        raise RuntimeError("the Wayback index is unreachable")
    stamps = [row[0] for row in r.json()[1:]]
    if verbose:
        print(f"  wayback: {len(stamps)} capture days {start}..{end}")
    for st in stamps:
        r = get(f"https://web.archive.org/web/{st}id_/https://{FPL_BOOTSTRAP}")
        if r is None:
            if verbose:
                print(f"  wayback: {st} unreachable, skipped")
            continue
        try:
            boot = r.json()
        except ValueError:
            # id_ serves the bytes as archived, which for this endpoint
            # is gzip without the header that would have unpacked it
            import gzip
            try:
                boot = json.loads(gzip.decompress(r.content))
            except (OSError, ValueError):
                continue
        obs = datetime.strptime(st, "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc).timestamp()
        yield obs, bootstrap_states(boot)


def id2code(data_dir, season):
    """element id -> stable code, from the season's raw file."""
    path = os.path.join(data_dir, f"players_raw_{season}.csv")
    out = {}
    for r in csv.DictReader(open(path)):
        try:
            out[int(float(r["id"]))] = int(float(r["code"]))
        except (KeyError, ValueError):
            continue
    return out


# ---------------------------------------------------------- the change log
def changelog(snapshots):
    """The change log from (observed_at, {code: state}) snapshots in time
    order: a row only when a player's state changes."""
    state, prev_start, out = {}, {}, []
    for obs, snap in snapshots:
        for code, (status, ch_this, ch_next, news, added) in snap.items():
            key = (status, ch_next, news)
            if state.get(code) == key:
                continue
            state[code] = key
            # Date the state from FPL's own publication time, but ONLY
            # when this row carries a news item of its own. When the news
            # field is empty the player has been CLEARED, and news_added
            # still points at the injury that has just ended - back-dating
            # to it would say he was fit from the day he got hurt. A
            # clearing is only knowable when the snapshot shows it.
            start = added
            fresh = (news and start is not None and start <= obs
                     and obs - start <= 400 * 86400)
            if not fresh:
                start = obs
            # states are strictly ordered in time for a given player
            p = prev_start.get(code)
            if p is not None and start <= p:
                start = obs if obs > p else p + 1.0
            prev_start[code] = start
            out.append(dict(code=code, observed_at=round(obs),
                            start_at=round(start), status=status,
                            chance_this=ch_this, chance_next=ch_next,
                            news=news))
    out.sort(key=lambda r: (r["code"], r["start_at"]))
    return out


def worst_gap(times):
    """(days, from, to) of the longest interval between consecutive
    snapshot times; (0, None, None) with fewer than two."""
    t = np.sort(np.asarray(times, float))
    if len(t) < 2:
        return 0.0, None, None
    d = np.diff(t)
    i = int(np.argmax(d))
    return float(d[i] / 86400.0), float(t[i]), float(t[i + 1])


def _day(ts):
    return f"{datetime.fromtimestamp(ts, timezone.utc):%Y-%m-%d}"


def harvest_season(repo, season, out_dir, elo_repo=None, wayback=(),
                   verbose=True):
    """Merge every source's snapshots in time order and write the change
    log. repo is the vaastav archive (None to skip it), elo_repo the
    FPL-Elo-Insights one, wayback a list of (start, end) YYYYMMDD
    stretches to pull from the Wayback Machine."""
    sources = []
    if repo:
        sources.append(("vaastav", vaastav_snapshots(repo, season)))
    if elo_repo:
        sources.append(("elo", elo_snapshots(elo_repo, season,
                                             id2code(out_dir, season))))
    for a, b in wayback:
        sources.append((f"wayback {a}..{b}", wayback_snapshots(a, b, verbose)))
    snaps, per = [], {}
    for name, gen in sources:
        k = 0
        for obs, snap in gen:
            snaps.append((obs, snap))
            k += 1
        per[name] = k
    if not snaps:
        if verbose:
            print(f"  {season}: no history in any source")
        return None
    snaps.sort(key=lambda x: x[0])
    out = changelog(snaps)
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, f"injuries_{season}.csv")
    with open(dest, "w", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS)
        w.writeheader()
        for r in out:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    if verbose:
        times = [o for o, _ in snaps]
        gap, a, b = worst_gap(times)
        nout = sum(1 for r in out if r["status"] in OUT_STATUS)
        src = ", ".join(f"{k} {v}" for k, v in per.items())
        print(f"  {season}: {len(snaps)} snapshots ({src}) "
              f"{_day(min(times))} to {_day(max(times))}, worst gap "
              f"{gap:.0f} days" + (f" ({_day(a)} to {_day(b)})" if a else "")
              + f", {len(out)} state changes ({nout} to an out status), "
              f"{len({r['code'] for r in out})} players -> {dest}")
    return dest


def report(data_dir, seasons):
    """Snapshot count, span and worst gap of each season's log."""
    print(f"{'season':8} {'rows':>6} {'snaps':>6} {'first':>10} {'last':>10} "
          f"{'worst gap':>10}")
    for s in seasons:
        path = os.path.join(data_dir, f"injuries_{s}.csv")
        if not os.path.exists(path):
            print(f"{s:8} (no log)")
            continue
        rows = list(csv.DictReader(open(path)))
        t = snapshot_times(data_dir, s)
        gap, a, b = worst_gap(t)
        print(f"{s:8} {len(rows):6d} {len(t):6d} {_day(t.min()):>10} "
              f"{_day(t.max()):>10} {gap:7.0f}d"
              + (f"  {_day(a)} to {_day(b)}" if a else ""))


def normalise(rows):
    """A player's states strictly ordered in time, whatever order they
    were appended in.

    The refresh runs that append to the live log can race: two runs
    queued off the same commit each append the state they saw, and the
    merge keeps both, so a later observation can carry the same start as
    the one before it (FPL had not touched news_added). Re-apply the
    harvest's rule in observation order - a start that does not follow
    its predecessor is dated from its own observation - so that every
    append also repairs whatever an earlier race left.
    """
    rows = sorted(rows, key=lambda r: (int(r["code"]), float(r["observed_at"]),
                                       float(r["start_at"])))
    prev, out = {}, []
    for r in rows:
        code = int(r["code"])
        start, obs = float(r["start_at"]), float(r["observed_at"])
        p = prev.get(code)
        if p is not None and start <= p:
            start = obs if obs > p else p + 1.0
        prev[code] = start
        r = dict(r)
        r["start_at"] = round(start)
        out.append(r)
    out.sort(key=lambda r: (int(r["code"]), float(r["start_at"])))
    return out


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
    rows = normalise(existing + [{k: ("" if v is None else v)
                                  for k, v in r.items()} for r in add])
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


def snapshot_times(data_dir, season):
    """Epoch seconds of the times the feed was read, sorted. The log is
    a change log, but with several hundred players nearly every read
    changes somebody, so its distinct observed_at values are the read
    times - short only of a read that changed nobody, which errs toward
    reporting a state as staler than it is, never fresher."""
    path = os.path.join(data_dir, f"injuries_{season}.csv")
    if not os.path.exists(path):
        return np.zeros(0)
    return np.array(sorted({float(r["observed_at"])
                            for r in csv.DictReader(open(path))}))


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
        # by start, then by observation: two rows can share a start when
        # FPL changes a status without touching news_added, and the one
        # seen later is the one that holds
        rows.sort(key=lambda r: (r[0], r[4]))
        a = np.array(rows, float)
        out[code] = a
    return out


FIT = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, float("nan"), 0.0)


def state_at(rows, t, snaps=None):
    """(factor, out, doubt, days_in_state, days_stale, known, return_at,
    return_known) at time t.

    Only intervals that had already STARTED by t are eligible: a state is
    read forward from its own start and never backward from a later
    observation.

    days_stale is how long since the feed last confirmed the state. With
    snaps (the feed's read times, from snapshot_times) that is t minus the
    last read at or before t: the log holds a row only when a state
    changes, so a state with no later row was confirmed by every read
    after its own, and a hole in the feed shows up as staleness growing
    through it. Without snaps it falls back to the time since the state
    was first observed.
    """
    if rows is None or len(rows) == 0:
        return FIT
    k = int(np.searchsorted(rows[:, 0], t, "right")) - 1
    if k < 0:
        return FIT
    start, f, out, doubt, obs, ret, known = rows[k]
    seen = min(obs, t)
    if snaps is not None and len(snaps):
        j = int(np.searchsorted(snaps, t, "right")) - 1
        if j >= 0:
            seen = max(seen, float(snaps[j]))
    return (float(f), float(out), float(doubt), (t - start) / 86400.0,
            (t - seen) / 86400.0, 1.0, float(ret), float(known))


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
    ap.add_argument("--elo-repo", default=None,
                    help="a clone of olbauday/FPL-Elo-Insights with history "
                         "(daily playerstats.csv, 2025/26 onward)")
    ap.add_argument("--wayback", nargs=2, action="append", default=[],
                    metavar=("FROM", "TO"),
                    help="also pull one Wayback Machine capture a day of "
                         "FPL's bootstrap-static between two YYYYMMDD dates; "
                         "repeatable")
    ap.add_argument("--live", action="store_true",
                    help="skip the harvest and just append the live game's "
                         "current state from the draft API")
    ap.add_argument("--report", action="store_true",
                    help="print each season's snapshot count, span and "
                         "worst gap, and exit")
    a = ap.parse_args(argv)
    if a.report:
        report(a.out, a.seasons)
        return 0
    if a.live:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from schwaddy import api
        append_bootstrap(a.out, api.draft_bootstrap())
        return 0
    if not (a.repo or a.elo_repo or a.wayback):
        ap.error("give at least one source: --repo, --elo-repo or --wayback")
    srcs = [x for x in (a.repo, a.elo_repo) if x] + \
           [f"wayback {f}..{t}" for f, t in a.wayback]
    print("harvesting injury history from " + ", ".join(srcs))
    for s in a.seasons:
        harvest_season(a.repo, s, a.out, elo_repo=a.elo_repo,
                       wayback=[tuple(w) for w in a.wayback])
    return 0


if __name__ == "__main__":
    sys.exit(main())
