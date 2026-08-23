"""Per-gameweek availability paths, replacing the single scalar.

Fixes three measured failure modes:
1. Long injuries: a flag with "Unknown return date" previously cost 35%
   spread over all gameweeks; now it zeroes the near term and recovers on
   an empirical profile. When the news string carries an explicit date
   ("Expected back 15 Sep"), availability is 0 until that gameweek, then
   ramps 0.5, 0.8 of baseline before returning to baseline.
2. Transfers: established players changing clubs get 0.68 of their prior
   minutes share over their first 4 gameweeks (n=126 movers vs 885
   stayers, 2021-2026), so a NEW_CLUB_GWS-long 0.68 discount applies to
   movers and debutants.
3. Doubtful flags apply only to the next gameweek, not the whole season.
"""
import re
from datetime import date

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}
NEW_CLUB_DISCOUNT = 0.68
NEW_CLUB_GWS = 4
UNKNOWN_PROFILE = (0.05, 0.15, 0.30, 0.45, 0.60, 0.75, 0.90)


def parse_return_date(news, ref_year):
    """'Expected back 15 Sep' -> date; None if no date present."""
    m = re.search(r"[Ee]xpected back (\d{1,2}) ([A-Za-z]{3})", news or "")
    if not m:
        return None
    day, mon = int(m.group(1)), MONTHS.get(m.group(2).lower())
    if not mon:
        return None
    year = ref_year if mon >= 7 else ref_year + 1   # season spans new year
    try:
        return date(year, mon, day)
    except ValueError:
        return None


def availability_path(status, chance, news, base, gw_dates, new_club=False):
    """Vector of per-GW availability over the remaining season.

    base: trailing-minutes availability (the healthy-state level)
    gw_dates: date of each remaining gameweek's first kickoff
    """
    n = len(gw_dates)
    path = [base] * n
    if new_club:
        for g in range(min(NEW_CLUB_GWS, n)):
            path[g] = base * NEW_CLUB_DISCOUNT
    if status == "u":
        return [0.02] * n
    if status == "d":
        path[0] = path[0] * ((chance / 100.0) if chance else 0.75)
        return path
    if status in ("i", "s"):
        ret = parse_return_date(news, gw_dates[0].year if gw_dates else 2026)
        if ret is not None:
            for g, d0 in enumerate(gw_dates):
                if d0 < ret:
                    path[g] = 0.0
                else:
                    k = sum(1 for d2 in gw_dates[:g] if d2 >= ret)
                    ramp = (0.5, 0.8)
                    path[g] = base * (ramp[k] if k < len(ramp) else 1.0) \
                        * (NEW_CLUB_DISCOUNT if new_club and g < NEW_CLUB_GWS else 1.0)
            return path
        if chance:                       # 25/50/75 short-term knocks
            path[0] = base * chance / 100.0
            return path
        for g in range(n):               # unknown return: empirical profile
            f = UNKNOWN_PROFILE[g] if g < len(UNKNOWN_PROFILE) else 1.0
            path[g] = path[g] * f
        return path
    return path
