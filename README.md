# schwaddy-fpl

Automated FPL **Draft** decision engine. Projections via low-rank matrix
completion (Athey et al. 2021) on the player-gameweek panel; decisions
(draft board, weekly XI, waivers, trades) chosen to maximise P(win) in a
6-team draft league with aggregate (classic) scoring. Dashboard served as
static JSON + HTML to justinwaddy.co.uk.

## Layout
- src/schwaddy/api.py         draft + classic API clients (public reads, no auth)
- src/schwaddy/draft_board.py v0 heuristic projections + VORP (21 Aug 2026 draft)
- src/schwaddy/mc.py          TROP-forecast estimator (port of justinwaddy/TROP v0.2.8, tau dropped, season+GW-of-season time effects, AR(1) factor extension into the forecast block)
- src/schwaddy/panel.py       five-season player-gameweek panel under draft scoring
- src/schwaddy/lineup.py      weekly XI + waiver optimiser (sessions 2-3)
- src/schwaddy/liveform.py    current-season minutes from the draft API
- src/schwaddy/overrides.py   manual status for transfers the API has not posted
- src/schwaddy/news.py        league news feed written to data/news.json
- .github/workflows/update.yml cron: 09:35 UK full refresh, five news checks through the day
- site/                       dashboard HTML; draft_room.html is the live draft tool

## Availability
P(plays) used to come from D over the last 8 archive matches, which fails
twice. The public gameweek archive does not publish the live season until
weeks in, so panel.build() runs with no live rows and availability is read
off last season - a player who has been an unused sub all season still
scores as a nailed starter. And D is a bare appearance indicator, so a
15-minute cameo counted the same as a full start.

liveform.py reads per-gameweek minutes from the draft API's live endpoints
instead, from match one, and splices them onto the archive. The estimate is
minutes played over minutes available across a trailing 8-match window,
live gameweeks first, so the archive drops out entirely once the season is
8 matches old. Blanks and doubles come off the fixture list, so a player is
only charged for minutes his team actually played.

Only availability reads this - the panel's Y and D, and so the fitted
projection, are untouched. Two behaviours are deliberate: a player with no
appearances last season (promoted clubs) is judged on live data alone
rather than charged for an archive he could not have played in, which makes
him more responsive to a single match than an established player is; and a
player the live data shows has not featured scores 0, not the debutant
prior. If the live endpoints fail, the whole path falls back to
archive-only rather than marking the entire league dropped.

## Overrides
status and news are the only live signal that a player has gone, and the
API can lag a completed transfer by days - during which a departed player
keeps his full projection and sits top of the XI. overrides.py forces
status for those players until the API catches up. Every applied override
is printed by refresh, and each entry carries the date it was added; delete
it once the API carries the news itself, since a stale override silently
outranks real data.

## News feed
The morning cron does the full refit and picks up overnight flag changes,
processed waivers and trades, and the official end-of-gameweek recap. The
other five crons run `refresh --news-only`, which skips the refit and posts
the matchday recap as the day's matches wrap up. 12:45 UK catches late team
sheets and morning flag changes; 14:45, 17:15, 19:45 and 23:20 UK each follow
a kick-off slot home (12:30, 15:00, 17:30 and 20:00 respectively): the live table, who
moved in it, a projected finish from the morning run's forecasts, and whose
players are still to come. Both append to data/news.json, which the News tab
reads directly. Everything is diffed against the previous run's state, so an
event fires once and re-running is a no-op.

## Lambda selection (backtest-informed)
CV is under the decision loss (realized XI points on placebo blocks), not
RMSE - see mc.TropForecast.cv_utility. Horizons differ: weekly XI and
waiver decisions use the utility-CV lambda_time (0.03 on 25/26 evidence);
season-ahead draft boards should use flatter recency (0.01), since heavy
discounting overweights end-of-season form at 38-GW horizons. Validated in
backtest.py (52.1 XI pts/GW vs 45.9 best benchmark) and draftsim.py
(22/30 simulated league titles, +171 pts vs best benchmark, t=7.1).

## Odds covariates (tested, off by default)
odds.py loads five seasons of Bet365 closing odds (football-data.co.uk,
100% name-match) and a the-odds-api.com live client (ODDS_API_KEY secret).
Backtested on 25/26: de-vigged 1X2 + over-2.5 covariates IMPROVE global
RMSE slightly but COST ~2 XI pts/GW (49.9 vs 52.1); interacting odds with
a premium-player indicator recovers to 51.5, still below no-odds. Fixture
sensitivity is heterogeneous in player quality, and team-level odds are
largely spanned by home + opponent-conceded at the top of the ranking, so
panel.build(use_odds=False) is the validated default. The genuinely
informative odds upgrade would be PLAYER-level markets (anytime scorer),
which lack a free historical source.

## Availability findings (tested, nothing shipped)
Empirical return ramp from 5 seasons of masked spells (absence >= 3 GWs):
mean points ratio to own baseline is 0.82 in the first match back and ~1.0
from the second match on. A first-match-back 0.82 discount changed ZERO of
38 backtest XIs: trailing-8 P(play) already excludes returning players at
the selection boundary, so the ramp is redundant with the availability
layer. The reverse fix (faster recency weighting, 3-GW window blended in)
is significantly WORSE (49.2 vs 52.0 XI pts/GW, t=-2.25): short windows
overreact to one-week rotation. Trailing-8 + API status flags stands.

## Validation across two seasons
Identical protocol, two held-out targets: 25/26 XI 53.6/GW vs 47.2 best
benchmark (+6.4); 24/25 XI 61.7/GW vs 54.1 (+7.6), with better RMSE and
rank correlation there too. The decision edge replicates out of sample.
Run: python -m schwaddy.backtest . 3  (season index, 4 = 25/26 default).

## Endgame and trades (modules, need league id + rival squads)
endgame.py: mean-variance XI search with Monte Carlo P(win). Measured
finding: XI-level variance tilting is worth ~nothing in draft (P(win)
flat in kappa, best ~0 whether trailing or leading by 40 with 9 GWs
left) because the squad is fixed and tilts swap only marginal players.
Rank-awareness should instead act through WAIVER/trade targets (acquire
high-sd players when trailing) - future extension, hook exists.
trades.py: model value vs consensus value (shrunk PPG x remaining);
suggest_trades finds offers that look fair on consensus but favour you
on model value.

## Pipeline (target state)
1. Pull draft bootstrap, league details, element-status (waiver pool), fixtures.
2. Rebuild the player-gameweek panel (5 seasons vaastav archive + live season).
3. Matrix completion projections under draft scoring; availability model from
   status/news fields.
4. Optimise XI, waivers, trade suggestions vs the 5 rivals' actual squads.
5. Write data/predictions.json; the static site reads it client-side.

## Needed from Justin
- Numeric draft league ID (in the URL at draft.premierleague.com once in the league)
- GitHub repo created with Actions enabled; push this scaffold
