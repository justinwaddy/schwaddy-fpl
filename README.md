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
- src/schwaddy/livegws.py     rebuilds the current season's gameweek file
- src/schwaddy/depth.py       club depth: minutes freed by flagged team-mates
- src/schwaddy/overrides.py   manual status for transfers the API has not posted
- src/schwaddy/news.py        league news feed written to data/news.json
- data/editorial.json         headlines written twice a day by a scheduled Claude session
- data/claims.json            Claude's five ranked waiver claims, same session, rendered on the Waivers tab
- src/schwaddy/weekly.py      live weekly league state, data/league.json
- src/schwaddy/compare.py     rival points predictions, data/compare.json
- src/schwaddy/playerstats.py season stats + match log behind the player card, data/player_stats.json
- .github/workflows/update.yml cron: 09:35 UK full refresh, three news checks; also on code pushes
- .github/workflows/pages.yml  publishes site/ to GitHub Pages on every site change
- site/                       dashboard HTML; draft_room.html is the live draft tool
- site/compare/               the same gameweek under four different predictors

## Availability
P(plays) used to come from D over the last 8 archive matches, which fails
twice. The public gameweek archive does not publish the live season until
weeks in, so panel.build() runs with no live rows and availability is read
off last season - a player who has been an unused sub all season still
scores as a nailed starter. And D is a bare appearance indicator, so a
15-minute cameo counted the same as a full start.

The trailing window used to treat a match from last May as evidence equal
to one from last Saturday, which drowns the opening weeks of a season in a
squad that no longer exists. A previous-season match now carries 0.1 of
the weight of a current-season one. Measured on the opening 8 gameweeks
across four season transitions, that cuts the Brier score by 14% overall
and 31% at gameweek 2, and improves all four transitions at every weight
tried between 0.5 and 0. From gameweek 9 the window is entirely
current-season and the weight has no effect at all; before a ball is
kicked it cancels, scaling numerator and denominator alike.

The floor on P(plays) for a player with no minutes behind him was 0.35.
Measured rolling-origin, that band actually appears about 9% of the time.
Sweeping it over 23/24, 24/25 and 25/26 cuts the Brier score by 36% at
0.15 in every one of the three. Realized XI points do not move (+0.95,
-1.45, +0.24 across those seasons - noise), because an XI is nailed
starters for whom the floor never binds. It is a fix to the number's
honesty, which is what the waiver comparison and the dashboard read.

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

## Opponent strength
The opponent term is a club's goals-conceded rate, season-to-date, shrunk
toward a prior. That prior used to be a flat league average of 1.4 with
weight 2, which threw away what was already known about a club and left
the covariate at the mercy of one match. Measured against what a club
actually concedes over the rest of the season, it was worse than using no
information at all early on (RMSE 0.469 after one gameweek, against 0.373
for a constant 1.4).

Anchoring each club to its own previous-season rate, promoted sides to the
80th percentile of last season's, at weight 16 - a single match's goals
conceded is far too noisy to move off a good prior - cuts that to 0.329
after one gameweek and 0.363 across all horizons, 16% better than the flat
prior, and it replicates at every season transition and every horizon
tested. Realized XI points do not move (-0.42, -0.47, +1.32 over three
seasons); per-match RMSE and rank correlation improve slightly.

This matters most now the live season is spliced in: with the flat prior,
one gameweek doubled the spread of the opponent term across all remaining
fixtures and left it correlated 0.27 with the settled rates it replaced.
At weight 16 the correlation is 0.94.

## The five-gameweek horizon
Everything the site ranked on - the waiver plan, the full board, the squad
card - used `rest`, the rest-of-season total. Over 38 gameweeks every club
plays every other one, so `rest` is very nearly fixture-free: it says who
the better player is and almost nothing about the run he is about to face.
The panel has always carried the schedule (home dummy plus the opponent
term above, read off the published fixture list for every future column),
so the information was there; nothing in the product was using it.

predictions.json now carries `next5` (the forecast summed over the next
five gameweeks, availability and match count included), `fix5` (what that
run is worth in points against the average run at the same position, from
the model's own fitted covariate slopes) and `run` (the opponents, upper
case for home, `-` for a blank). Waivers rank on `next5`.

Rolling-origin over 23/24, 24/25 and 25/26: rank the league at each origin
gameweek, then score what those players actually made over the next five.

|                  | rank rho vs realized | top-20 shortlist, realized pts |
|------------------|----------------------|--------------------------------|
| next5            | .653  .694  .618     | 22.77  23.64  21.37            |
| rest (old)       | .600  .642  .556     | 22.11  22.74  20.54            |
| next GW x5       | .553  .593  .541     | 21.37  22.01  19.80            |

Three seasons out of three: t = 12.5 to 13.8 on the correlation, 2.8 to
4.1 on realized points, worth about +0.8 points per player claimed. The
third row is the control - repeating one gameweek five times is the WORST
of the three, so it is the schedule doing the work, not the shorter
horizon.

## Opponent attacking strength (tested, REJECTED)
The opponent term is only the club's CONCEDED rate, which is the wrong
side of the ball for a defender or a keeper. Pooled over five seasons the
two slopes differ by position and by sign - conceded/scored is +0.37/-0.70
for GKP, +0.27/-1.09 for DEF, +0.46/-0.37 for MID, +0.56/-0.29 for FWD -
so for a defender the opponent's ATTACK is four times the term in use and
is not carried at all.

Adding it (panel.build(fixture_mode="both")), and adding it interacted
with position ("pos"), both improve per-match RMSE and the within-player
rank correlation slightly, and both LOSE realized XI points in every
season tested: -3.68/-3.45/-4.45 and -3.47/-3.50/-6.13 over 23/24, 24/25
and 25/26. Three out of three, most t-stats past -2. panel.build defaults
to fixture_mode="basic".

This is the same result the odds experiment below reached by a different
route, and for what looks like the same reason: the low-rank block already
carries team-level attacking and defensive strength, so an explicit
opponent-attack term double-counts it and puts swing into the forecast
that reorders the top of the board without being right often enough to pay
for the churn. Accuracy on the average player and accuracy on the eleven
you actually pick are not the same thing.

## The live season
The public archive does not publish a season's per-gameweek rows until
weeks into it, so build() spent the opening weeks fitting on last season
and earlier - blind exactly when squads have just changed. Measured
rolling-origin over 25/26, with the target season masked to reproduce
that blindness, it costs 2.95 realized XI points a gameweek across the
season and 6.40 over its first ten.

Everything the archive file holds is served live by the API, so
livegws.py writes the same file from it and build() reads it unchanged.
Settled gameweeks are frozen once written: the club a player belongs to
comes from the season's raw file, which holds only his current one, so
rebuilding an old gameweek after a January move would file those matches
under the wrong club. The newest finished gameweek is always refetched,
since bonus and corrections land late. Once the real archive appears,
pull() takes it and the reconstruction stands down.

## Club depth
Minutes share says whether a player has been playing, never what happens
when the man ahead of him stops - a backup and a first choice just back
from injury look identical at a third of the minutes. depth.py groups
players by club and position and hands the minutes of anyone flagged out
to the team-mates who are fit, in proportion to what they already play,
capped at double. With a full-strength group every multiplier is 1.0, so
the ordinary case is undisturbed.

Measured rolling-origin over 25/26, with three straight unused gameweeks
standing in for the status flags the archive does not carry: XI decisions
+0.08 +/- 0.75 a gameweek, i.e. nothing, since the XI is nailed starters
who never get a boost. The gain is calibration on the 8427 player-
gameweeks it does touch, where the model under-predicted appearance by
4.8 points (0.709 against an actual 0.757); depth halves that to 2.4.

## Overrides
status and news are the only live signal that a player has gone, and the
API can lag a completed transfer by days - during which a departed player
keeps his full projection and sits top of the XI. overrides.py forces
status for those players until the API catches up. Every applied override
is printed by refresh, and each entry carries the date it was added; delete
it once the API carries the news itself, since a stale override silently
outranks real data.

## Hosting

site/ is published to GitHub Pages by .github/workflows/pages.yml: it uploads
the directory as-is on any push to main that touches site/, and on manual
dispatch. Live at https://justinwaddy.github.io/schwaddy-fpl/, with
/compare/ and /draft_room.html alongside it.

One switch has to be flipped by hand, once: Settings -> Pages -> Build and
deployment -> Source -> "GitHub Actions". Until that is set the workflow
fails at the deploy step. The repo also has to be public, or on a plan that
allows Pages for private repos.

Nothing under data/ is bundled into the deploy. The pages fetch predictions,
news and league state from raw.githubusercontent.com at load time, so the
bot's refresh commits show up on the live site without a redeploy - and the
cron pushes, which only ever write data/, never retrigger this workflow.

## Live scores
The cron is a batch pipeline - four runs a day, each minutes late, read
through a CDN that caches for five more - so nothing that goes through it
can be live. The Live tab does not go through it. It polls a Cloudflare
Worker (live/worker.js, free tier) every 15 seconds while a match is on,
sleeps until the next kick-off between matches and stops when the
gameweek is over; the worker exists only because FPL's API sends no CORS
headers, so it reads
the live feed server-side, trims it to the six squads and hands the page one
13KB JSON. Points, provisional bonus (3/2/1 on bps with FPL's tie rules,
until the official bonus lands) and provisional subs are worked out in the
page under the same rules as weekly.py, so the Live tab and the League tab
agree once a gameweek settles: checked against GW2, every manager's score
and bench match league.json exactly. The model is never re-scored live;
only the scoreboard is. Deploying the worker is a one-off, five-minute
job described in live/README.md; until its URL is pasted into LIVE_URL in
site/index.html the tab shows the cron's last snapshot and says so.

## Comparing predictions
site/compare/ puts three rivals beside the model's number for the coming
gameweek: FPL's own ep_next from the classic API, the season-to-date mean
shrunk toward last season (backtest.py's B1), and the mean of the last
four played matches (B2). B1 and B2 are per-appearance rates, multiplied
by the same availability the model applies, so no column is flattered by a
player who scores well and rarely plays.

ep_next is computed under classic scoring rather than this league's draft
scoring - the two tables overlap but are not identical, a keeper's goal
being 10 here against 6 there - so it is an outside opinion, not a
like-for-like number. The page says so where it is read.

The interesting column is the spread. Sorting by it puts the players the
four most disagree about at the top, and it is almost always B2: form
chases a hot streak that the model's shrinkage is built to damp.

## The weekly league
weekly.py writes data/league.json every run: each manager's live score,
how many of his starters are still to come, what the week projects to once
they play, and his full squad for the dashboard's click-through.

Substitutions are provisional. The game only applies them when the
gameweek ends, but a starter whose match has finished with no minutes is
already lost, so his replacement is worked out under the same formation
rules the game enforces - keepers only for keepers, bench order respected,
minimums and maximums held. That makes the live table read like the final
one instead of punishing a manager for a blank he covered on the bench. A
substitution is accepted when it does not increase the formation's breach
of the rules rather than when it leaves it strictly legal, so a lineup
that arrives malformed still gets a best effort instead of silently
getting none.

The news feed quotes the same table, so the tab and the feed cannot
disagree. Events carry a scope - "mine", "league" or "free" - which drives
the three-way filter on the news tab.

## News feed
The morning cron does the full refit and picks up overnight flag changes,
processed waivers and trades, and the official end-of-gameweek recap. The
other three crons run `refresh --news-only`, which skips the refit and posts
the matchday recap as the day's matches wrap up: 14:45, 18:00 and 23:20 UK,
each following a kick-off slot home (12:30, 15:00 and 20:00 respectively).
A push touching src/ also triggers a full refresh, so a code change is
validated against real data instead of waiting on the next cron - the
commit step only writes under data/, so the bot cannot retrigger itself.
Scheduled runs have been landing hours late, so read the cron times as
"some time after", not as clock times: the live table, who
moved in it, a projected finish from the morning run's forecasts, and whose
players are still to come. Both append to data/news.json, which the News tab
reads directly. Everything is diffed against the previous run's state, so an
event fires once and re-running is a no-op.

## Claude's five claims
The Waivers tab used to hold one opinion, the model's: rank free agents on
next5, swap in whoever clears the weakest at his position by two points.
That board cannot see a surgery, a manager's quote on minutes, a signing
walking straight into the XI or a loan out of the league - exactly the
things that decide whether a claim is good. The editorial headlines put
that knowledge in the feed; this puts it on the decision.

data/claims.json is written by the same scheduled Claude session as the
headlines, twice a day, from one round of research. It carries up to five
add/drop pairs in submission order - waivers process by priority and a
successful claim sends you to the back of the queue, so the pair you most
want goes first - each with the reasoning, one sentence on what the model
says and whether it is being overruled, a confidence, and the articles
behind it. The card renders that above the model's board as "Claude: make
5 claims this week", with the waiver deadline in UK time and, beside every
swap, the model's own gain looked up live from predictions.json by player
code, so the number stays current between research runs. A swap that is
also on the model's board is tagged with its rank there; a player who has
since been claimed, or dropped, is flagged.

The file is the third writer into data/ and the same rule applies as to
editorial.json: only the scheduled session writes it, the refresh cron
never touches it, and the site reads it straight from raw so it is live
the moment it is pushed. The session validates before it commits - every
add is a current free agent and every drop is one of ours, add and drop
share a position because the squad is full, a player appears in one claim
only, names match predictions.json exactly - so an invented player cannot
reach the page. When the gameweek moves on the old set is kept under
"previous", six deep, which is the record for measuring the claims later.
If the file is missing the card says so and the model's board stands
alone.

## The player card
Every player name on the dashboard opens a card: what the model expects of
him, what he has actually done this season, and whatever the feed has said
about him. It is one delegated click handler over the whole page, so the
squad XI, the bench, the waiver board, the full player table, any manager's
expanded squad in the League tab and every name in the Live tab - squads
and fixture chips alike - all open the same card. predictions.json players
carry `code` and the league and live squads carry the element `id`; either
resolves through the id-to-code index the stats file provides.

The projection half is already on the page - it comes out of
predictions.json. The other half is data/player_stats.json, written by
playerstats.py on every refresh: season totals and set-piece duties off the
draft bootstrap, plus a match log read out of the current season's gameweek
file. The log holds appearances only, capped at the last 15 of them - the
gameweek file carries a row per player per match whether he played or not,
and fifteen rows of zeros is not a match log - and the rows are bare arrays
under a `log_cols` header, which is about a third of the bytes of a dict
per row. The whole file is a quarter of a megabyte and is fetched on load,
because the club names in every table come out of it too: predictions.json
carries the club as a team id, which the site used to print raw.

The news half is matched client-side. Feed events are free text, and the
feed writes a player the way the site does - "Watkins (AVL): ..." - so a
card scans the text for his name on word boundaries, accents normalised
away. Where the text tags a club straight after the name it has to be his
club, which separates two players sharing a web name. Managers are named in
the feed too and a manager and a player can be the same word, so where they
collide the club tag is required rather than optional: "Justin 43" in a
scoreline is not news about the Leeds full back, and the 21 scorelines that
used to land on his card no longer do.

The open card lives in the URL hash, so a card survives a reload, can be
sent to someone, and the back button closes it. Everything degrades: if
player_stats.json fails to load the card still shows the projection, and if
predictions.json fails it still shows the stats.

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
