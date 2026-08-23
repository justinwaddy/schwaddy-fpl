"""Public FPL Draft + Classic API clients. No auth required for reads."""
import requests

DRAFT = "https://draft.premierleague.com/api"
CLASSIC = "https://fantasy.premierleague.com/api"

def get(url):
    r = requests.get(url, timeout=30, headers={"User-Agent": "schwaddy-fpl"})
    r.raise_for_status()
    return r.json()

def draft_bootstrap():      return get(f"{DRAFT}/bootstrap-static")
def draft_game():           return get(f"{DRAFT}/game")
def league_details(lid):    return get(f"{DRAFT}/league/{lid}/details")
def element_status(lid):    return get(f"{DRAFT}/league/{lid}/element-status")
def entry_event(eid, gw):   return get(f"{DRAFT}/entry/{eid}/event/{gw}")
def classic_bootstrap():    return get(f"{CLASSIC}/bootstrap-static/")
def fixtures():             return get(f"{CLASSIC}/fixtures/")
def element_summary(pid):   return get(f"{CLASSIC}/element-summary/{pid}/")
