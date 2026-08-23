"""Bookmaker odds covariates.

Historical: football-data.co.uk per-season E0.csv (Bet365 closing odds).
De-vig 1X2 into win/draw/loss probabilities and the over-2.5 market into
p_over. Cell covariates for player i in match m: p_win (i's team),
p_opp_win, p_over.

Live: the-odds-api.com client (free tier). Set ODDS_API_KEY in the
environment (GitHub Actions secret). Only the next gameweek or so is
posted, so future cells beyond the odds horizon fall back to centered
league-average values, which zeroes the odds terms there and leaves the
opponent-strength covariate carrying fixture information.

Backtest caveat, stated once and honestly: closing odds finalize at each
match's kickoff, which for late-weekend games is AFTER the FPL deadline.
Backtests using closing odds therefore overstate the live edge slightly,
mainly through post-deadline team news. Live operation uses odds as of
the pull time before the deadline.
"""
import csv
import numpy as np

# football-data team name -> vaastav/FPL team name
NAME_MAP = {
    "Man United": "Man Utd", "Man City": "Man City",
    "Nott'm Forest": "Nott'm Forest", "Sheffield United": "Sheffield Utd",
    "Newcastle": "Newcastle", "Tottenham": "Spurs", "Wolves": "Wolves",
    "Luton": "Luton", "Leeds": "Leeds", "Leicester": "Leicester",
    "Ipswich": "Ipswich", "Brighton": "Brighton", "Bournemouth": "Bournemouth",
    "West Ham": "West Ham", "Aston Villa": "Aston Villa",
    "Crystal Palace": "Crystal Palace", "Everton": "Everton",
    "Fulham": "Fulham", "Brentford": "Brentford", "Arsenal": "Arsenal",
    "Chelsea": "Chelsea", "Liverpool": "Liverpool", "Burnley": "Burnley",
    "Southampton": "Southampton", "Watford": "Watford", "Norwich": "Norwich",
    "Sunderland": "Sunderland", "Coventry": "Coventry", "Hull": "Hull",
}

BASELINE = dict(p_win=0.36, p_opp_win=0.36, p_over=0.52)


def _devig(h, d, a):
    inv = 1 / h + 1 / d + 1 / a
    return (1 / h) / inv, (1 / d) / inv, (1 / a) / inv


def load_historical(data_dir, seasons):
    """(season_idx, home_name, away_name) -> dict(ph, pd, pa, p_over)."""
    out = {}
    for si, s in enumerate(seasons):
        for r in csv.DictReader(open(f"{data_dir}/odds_{s}.csv",
                                     encoding="utf-8-sig")):
            try:
                ph, pd_, pa = _devig(float(r["B365H"]), float(r["B365D"]),
                                     float(r["B365A"]))
                over, under = float(r["B365>2.5"]), float(r["B365<2.5"])
                p_over = (1 / over) / (1 / over + 1 / under)
            except (ValueError, KeyError, ZeroDivisionError):
                continue
            hm = NAME_MAP.get(r["HomeTeam"], r["HomeTeam"])
            aw = NAME_MAP.get(r["AwayTeam"], r["AwayTeam"])
            out[(si, hm, aw)] = dict(ph=ph, pd=pd_, pa=pa, p_over=p_over)
    return out


def cell_covariates(match, team_is_home):
    """Return (p_win, p_opp_win, p_over) for one side of a match."""
    if match is None:
        return BASELINE["p_win"], BASELINE["p_opp_win"], BASELINE["p_over"]
    if team_is_home:
        return match["ph"], match["pa"], match["p_over"]
    return match["pa"], match["ph"], match["p_over"]


def fetch_live(api_key, regions="uk", markets="h2h,totals"):
    """Pull upcoming EPL odds from the-odds-api.com. Returns raw JSON;
    refresh.py maps events to fixtures by team names and kickoff."""
    import requests
    r = requests.get(
        "https://api.the-odds-api.com/v4/sports/soccer_epl/odds",
        params=dict(apiKey=api_key, regions=regions, markets=markets,
                    oddsFormat="decimal"),
        timeout=30)
    r.raise_for_status()
    return r.json()
