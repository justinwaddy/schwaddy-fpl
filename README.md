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
- data/starters.json          will each of my players start: weekly deep research, first run after waivers
- src/schwaddy/weekly.py      live weekly league state, data/league.json
- src/schwaddy/compare.py     rival points predictions, data/compare.json
- src/schwaddy/playerstats.py season stats + match log behind the player card, data/player_stats.json
- src/schwaddy/prices.py      classic-game prices, the market's read beside the model's; data/prices.json, the sortable market table on the Waivers tab
- .github/workflows/update.yml cron: 09:35 UK full refresh, three news checks; also on code pushes
- .github/workflows/pages.yml  publishes site/ to GitHub Pages on every site change
- cron/worker.js              Cloudflare cron that dispatches the refresh on time
- src/schwaddy/public.py      model-free data/public.json for the per-manager sites
- site/team.js, site/team.css shared front end for site/<manager>/
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

## When the archive lags
pull() used to stand down from reconstructing the live season the moment
the public archive published anything for it. The archive published
gameweek 1 and stopped, which overwrote the reconstruction with a file one
gameweek short and then skipped rebuilding it. The panel fitted without the
newest gameweek, availability read it, and the match logs behind every
player card ended a week early - and it would have stayed that way all
season, since the archive always lags.

The reconstruction now runs every time. livegws.write keeps the rows
already in the file and only adds the finished gameweeks it is missing, so
this is strictly additive: the archive still owns the gameweeks it has
published, it just no longer holds the newest one hostage. The refresh
prints which gameweeks are present and which it had to add.

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
dispatch. Live at https://justinwaddy.github.io/schwaddy-fpl/.

The root is a landing page listing the six managers, since the other five
now have links to this site and the base URL is where they will land. The
full engine dashboard sits at /justino/, with /compare/ and
/draft_room.html alongside it at the root as before. The landing page does
not link /justino/: it is a public URL like everything else here, but it
is not advertised to the league.

One switch has to be flipped by hand, once: Settings -> Pages -> Build and
deployment -> Source -> "GitHub Actions". Until that is set the workflow
fails at the deploy step. The repo also has to be public, or on a plan that
allows Pages for private repos.

Nothing under data/ is bundled into the deploy. The pages fetch predictions,
news and league state from raw.githubusercontent.com at load time, so the
bot's refresh commits show up on the live site without a redeploy - and the
cron pushes, which only ever write data/, never retrigger this workflow.

## The other five managers
site/ed/, site/rob/, site/bben/, site/sben/, site/justin/ and site/marcus/
are one page each for the six managers in the league: My squad, Live,
News, League and All players, and nothing else. No waiver board, no
starters, no suggested XI, no projections anywhere.

Leaving a column out of a table would not have been enough. The page
fetches its data, so anything the file carries is one devtools tab away
from the reader whether it is rendered or not. So the sites read
data/public.json and nothing else from the pipeline, and public.py builds
that file from a whitelist of fields rather than by deleting the ones that
must not travel: a whitelist cannot leak a covariate somebody adds
upstream next month. What survives is what the game itself publishes -
ownership, live and settled scores, the table, each player's counting
stats and his next fixture. What does not: everything in predictions.json,
and the model columns league.json carries alongside the real ones.

league.json holds the squads as they were for the gameweek it scored, and
waivers process a day before the next deadline, so between the two a
manager looking at his own page would have seen players he no longer had.
Each manager therefore carries his roster as it stands now as well, and
the page leads with that; the gameweek eleven sits underneath it, labelled
as the week it scored.

Clicking any player name on any of the six pages opens a card: his season
totals and every match he has played, straight from the game. The stats
file behind it is a quarter of a megabyte and most visits never open a
card, so it is fetched on the first click rather than on load. Column
headings carry a tooltip saying what the column is, which the three-letter
ones need.

The six pages share site/team.css and site/team.js. Each index.html is
twenty lines that name the manager and load them, so there is one copy of
the logic. Prices are the one thing that differs: only Ed's page fetches
data/prices.json, and only Ed's page has the value columns.

### The News tab opens on the league
The tab order is News, Live, My squad, League, All players, and News opens
on its League chip rather than on everything.

That chip is not only the league's own doings. It also carries every player
item about somebody one of the six holds, or one of the fifty best players
in the game on points - a hamstring at the top of the board is a waiver
claim whoever owns him - so the one view answers "what happened that
affects us". The Players chip is still everything, ours or not.

Every name in the feed opens the card.

The names in the News tab are links too. The text is escaped first and the
names matched against the escaped form, so the linking can never introduce
markup the feed did not have. Two traps were worth the trouble: a bare
first name followed by another capitalised word is left alone unless the
pair is itself a player the game knows, which is what stops "Enzo Maresca"
becoming a link to Chelsea's Enzo; and a manager's name always wins over a
player's, because James Justin is a real Leicester defender and "Justin"
in this feed means the manager, twelve times on one screen.

The League tab itself is the table. On the day of the first kick-off it
stops being about last week.
Every gameweek column reads zero, the header says when the first match is,
and opening a manager shows the fifteen he holds rather than the eleven
that played six days ago. The live feed takes over at kick-off. The league
plays for a pint a week, so a week that has not started is nil-nil.

Ben C and Ben D are Small Ben and Big Ben, which is what the league calls
them. The renaming is done on the page rather than in the data, so it
covers public.json, the live feed and the gameweek archive from one place,
and the feed's prose gets it too, since the news is written with the
game's names in it.

### The player card
Clicking a name anywhere opens it: the league's own headshot where one
exists - keyed on the same player code this file is keyed on, and the
element removes itself on the 404 a recent signing usually gets - his
fitness note, his season, where he ranks on points overall and in his
position, what the game's draft ranking made of him, the numbers under the
points, and every match he has played.

### Looking back at a gameweek
The tabs only ever knew the gameweek the cron last scored, which is the
right thing for a live table and useless for the argument a six-man league
actually has on a Sunday. The second card on My squad now steps through
every finished gameweek - arrows either side, a dropdown between them - and
shows the squad that played it, what each man returned, the automatic
substitutions the game made, and where that week finished.

data/gw_history.json holds it. A gameweek that has been scored never
changes, so each one is fetched once, from the draft API's own picks, and
the file is only ever appended to: six calls a week rather than six hundred
on every run. The points come out of player_stats.json, which already
carries every player's match log, so no second live call is needed. Checked
against league.json for GW2: all six managers' scores and bench points
match exactly. It is a quarter of a megabyte by May and most visits never
press an arrow, so the page fetches it on the first press rather than on
load, the same bargain as the stats file behind the player card.

Building it turned up a real bug in the match logs. gws_2026-27.csv carries
the classic game's element id, and player_stats.json is keyed on the draft
id. The two agree for everyone registered before the season - both games
number that intake identically - and diverge for the 52 players added
since, so those cards were showing somebody else's matches: Matt Targett's
were filed under Mamadou Sangare, and Sangare's own GW2 was missing, which
is what made a manager's reconstructed week come out three points light.
The rows are keyed by the draft id the full name resolves to now, falling
back to the column only when the name is unknown.

### What the league table says
The gameweek columns were points, bench points and "to play", and by the
time anyone read them two of the three were blank: "to play" is zero once
a week is over, and bench points is the one number in draft you cannot do
anything about. They are gone. What is there now is the week itself - the
points, where he finished it, how many of his fifteen got a minute, how
many got none, the automatic substitutions the game made for him, his best
scorer, and what the best legal eleven out of his fifteen would have added,
which is the whole of what selection cost him. Then his squad's injuries as
they stand today, and the season total.

Zeros are printed as zeros. A blank cell reads as a broken column, which is
how the old one read.

The gameweek half of the table now comes from the live feed whenever the
feed is ahead of the cron - a started fixture in a gameweek at least as new
as the one public.json scored - so on a Saturday it moves every fifteen
seconds instead of four times a day. The season column stays on
public.json, because the game only moves that when it processes the week,
and a settled gameweek defers to public.json entirely, including its
tie-breaks.

Every table also carries a line above or below it saying what each column
is. The tooltips are still there, but a tooltip is nothing on a phone and
that is where these are read.

### The Live tab, on the six pages
The tab is always there, and its bottom half is the gameweek's fixture
list: every match in kick-off order, the crests, the score or the kick-off
time, and under each one every player in the league who is in that match:
his name, his owner, and his points once it has started. The reader's own
are outlined and sort to the front. A count would have fitted in less
room, but which of your own players have a game is the thing worth
knowing, and only the names say it. They are clickable through to the
player card, same as every other name on the page.

The header above all of it leads with what the reader came for: his points
this gameweek, where that puts him, and the gap to the leader, then a line
saying what the feed is doing.

Both the header and the ticker are per gameweek, because the league plays
for a pint a week and last week's total is last week's. The feed disagrees
for four days of every seven - it keeps reporting the finished gameweek
until the next deadline - so once its fixtures are all final the tab reads
zero for everybody and says nobody has scored yet, ordered on the season
table, rather than showing Monday's scores as though they were Saturday's.

Which gameweek that is takes some care. The live feed reports whichever
one the game itself calls current, which from the last whistle on Monday
until Friday's deadline is the week that has just finished - so a tab
built only on the feed would spend half of every week showing last week's
results. public.json therefore carries the coming gameweek's fixtures, and
once every match in the feed is final the list switches to those: no
scores, just the kick-offs and who owns whom. It hands back to the feed
when the deadline rolls it forward.

What comes and goes is the live half above it. A ticker runs under the
header, the six managers in live-points order,
re-rendered whenever the standings change and left alone when they do not,
so the marquee does not jump back to the start every fifteen seconds. It
respects prefers-reduced-motion.

Above the fixture list, while a match is on, is one card per match in
play: the two crests either side, the score between them and the minute
under that. Each side lists only the league's players in that match - the
rest of the twenty-two is on television - and each row carries his points,
who owns him, whether he is in that manager's counting eleven or on the
bench (with an arrow where an automatic substitution moved him), what he
is doing in the match - on, subbed off, on as a sub, not on - how many
minutes he has, and what he has actually done: goal +6, 4 saves +1, 2
conceded -1. Opening the row gives the same breakdown in full, with the
counts and the game's own wording.

That comes from the API's explain block, which the live worker passes
through per fixture. Whether a man started needs the `starts` stat as
well: minutes alone cannot separate someone who came on from someone who
has been substituted off, since both sit below the match clock. Against an
older worker that does not send it the page falls back to the guess and
simply cannot label a substitution.

Bonus is the awkward one. The official three-two-one lands minutes after
the whistle, so until then the card shows the provisional bonus computed
from bps under the same rules as everywhere else - and suppresses it the
moment the breakdown itself names a Bonus row, or the two would be added
together and every hauling player would read three points high.

Multiple matches mean multiple cards, ordered by how many of the reader's
own players are in each, so his game is the one at the top.

A card disappears at its own final whistle - it is the live half, and that
match is no longer live. Everything it was showing survives in the fixture
list underneath, which flips that row to FT with the score and keeps every
player's points on it, and in the ticker, which keeps the running weekly
total. With nothing in play at all the cards are simply absent and a line
says when the next kick-off is, or that the gameweek is over.

When the week itself is over the tab does not immediately forget it. Both
this tab and the League table turn to the next gameweek on the day of its
first kick-off, not at the last whistle - the same rule in one function so
they cannot disagree - because the week just gone is the one still being
argued about on the Tuesday, and the pint is settled on it. Until then the
ticker holds the final standings and the fixture list holds ten FT rows.

These are public URLs on GitHub Pages. Anyone who guesses another
manager's path can open it, which changes nothing except that they would
see that manager's squad tab and, on Ed's, the prices. The engine's output
is not on any of them, which is the part that matters.

## League news, and the roast box
The News tab opens with the deadline: which gameweek, what time line-ups
lock in UK time, and how long is left. It is computed on the page from the
deadline in public.json rather than written into the feed by anybody, so
it cannot say "closes today" on a Sunday, and once the deadline passes it
says so and explains that the eleven standing at the deadline is the one
that scores.

Under it are two feeds side by side, newest first, with three chips: all
news, the league, or the players. A waiver is a manager's doing, an
opinion is about a manager and a scoreline is the competition; a
hamstring, a hat trick and a transfer belong to the player. The suggest
button sits on the right of that same row rather than under a heading of
its own.

The league's own running record comes through public.json: who claimed
whom, who is injured or cleared, who hauled, who left points on the bench,
how the table moved, and the editorial lane's football news with the
article behind it. Almost all of it travels - 203 of 220 events.

The exception is the research into Justin's own starting eleven, which is
the one thing in the feed that is genuinely an edge. Headlines, squad
notes and projections carry a scope, and news.py sets it to "mine" exactly
when the item is about his own squad, so that is the line: those stay
behind and the same kinds scoped to the league or a free agent travel. The
scope test is applied only to those three kinds, because it is set loosely
elsewhere - the gameweek scoreboards are "mine" too, and using it
generally would drop half the league's results.

Two guards sit behind that. Anything addressed to him directly is cut,
whatever it is filed under, since "Your GW2 best: Haaland 13" arrives as a
score. And a sentence discussing the forecast is removed from an item
rather than the item being dropped: the editorial lane often closes a
piece of real news with a line on what the model makes of the player, and
losing the Barcola signing over its last sentence would be a poor trade.
An item cut down to nothing goes entirely.

data/league_news.json is the second feed, written once a day by its own
scheduled session, separately from the editorial lane. Every reported item cites the article it came from, from a
named outlet, so a claim is one click from its source; the session is
barred from sportsmole and from rumour aggregators, and barred from
reading any of the engine's files at all, so the page cannot quietly hand
one manager an edge.

On a matchday it also writes one to three OPINION pieces of its own. Those
draw on data/roasts.json, an archive the managers fill themselves: the
News tab has a box where anyone can name a manager and suggest a line.
Every one of the six can be named, including whoever's page you happen to
be on: these pages are public and get passed around, so excluding the
owner only meant Ed could not be roasted from Ed's page. The pages are static, so the box posts to the Cloudflare worker,
which asks GitHub to run .github/workflows/suggest.yml, which appends it.
The worker needs no permission beyond the Actions write it already had for
the schedule - the committing is done by the runner with its own
credentials, and everything arriving from that public endpoint is capped,
scrubbed and matched against the six managers before it is written.

The opinions are football only, about squads, results, transfers and the
standings, and never about anything a person did not put into the league.
Suggestions are marked used once they run, so a line does not come round
twice, and the archive is never published - the pages show the opinion,
not who asked for it.

## Scheduling
GitHub's `schedule:` crons are queued on shared runners and arrive when
GitHub gets round to them. Over twelve consecutive scheduled runs of
update.yml the median was 152 minutes late, the worst 305, none inside
five minutes, and the 13:45 UTC slot never fired at all - GitHub drops a
scheduled run rather than queueing it when the pool is busy. A dashboard
that exists to be right before a deadline cannot be built on that.

cron/worker.js is a Cloudflare Worker on Cron Triggers, which do fire on
time, and it dispatches update.yml through the API, which starts a run
within seconds instead of queueing it. It asks GitHub when the workflow
last ran and skips if that was under 20 minutes ago, so the backstop cron
and a push cannot stack two refits on each other. The GitHub schedule
stays as that backstop until the worker has proved itself, and the
worker's status page says whether its token is configured. Deploy is a
one-off five-minute job described in cron/README.md; until it is done the
GitHub crons run alone, late, exactly as before.

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
and bench match league.json exactly.

Before the deadline the worker still reports the finished gameweek, because
FPL hides the coming week's line-ups until the deadline passes and there is
nothing to score. The fixture list and the ownership are known, though, so
from the moment the current gameweek is final the tab previews the next
one from the cron's data: the deadline and first kick-off, each manager's
count of players with a game, and every fixture with the league's players
in it. It hands over to the live feed on its own when the worker rolls
forward at the deadline.

Ownership is the one thing in predictions.json that changes between
refits: waivers process about a day before the deadline, and the next
full refresh is the following morning. The matchday runs now carry fresh
ownership into predictions.json in place (refresh._sync_owners), leaving
every projection untouched, and there is an extra news run at 18:45 UK on
Thursdays, fifteen minutes after the usual waiver time. So the squad, the
waiver board, the Starters tab, the Live preview and the scheduled
session's own brief all see the post-waiver squads within the hour rather
than the next morning. The model is never re-scored live;
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

## Sources
Everything the two research Routines cite comes off one list: BBC Sport,
The Guardian, Sky Sports, premierleague.com, ESPN, Reuters, AP, The
Athletic, or the club's own site. Each of the three validators - headlines,
claims, starters - and the league news one parse the URL's host and refuse
anything else, so an off-list citation cannot be published rather than
merely being discouraged.

It used to be a blocklist, and a blocklist loses. Sportsmole was named in
the league news prompt and nowhere else, so the run that wrote Justin's own
tabs used it twenty-two times, alongside rotowire, yahoo, a Detroit
television station and a fantasy site whose predicted XI is copied off Sky
a day late. Twenty-two of the fifty citations in the repo came from one
outlet nobody had approved, because nothing had thought to forbid it. A
whitelist has the opposite failure mode: a good outlet nobody listed gets
left out, which costs a headline rather than the credibility of the page.

The fifty off-list citations already published were purged when the rule
came in - seventeen headlines whose only source was off the list, and
twenty-three links under the starters. Twelve players had been called
starters on that evidence alone; they are now "likely", which is what an
unsourced opinion about a lineup actually is. A player no permitted outlet
has written about can never read "start", which the validator enforces
too, so the tab cannot quietly refill with the same thing.

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

## Starters, once a week
Waivers process about a day before the deadline, and from then until the
deadline the squad is the squad. That is the one moment in the week when
the question "will each of my fifteen actually start" is both answerable
and worth answering, so that is when it is asked. The scheduled session's
brief works out from the draft bootstrap whether it is inside that window
(waivers_time passed, deadline_time not) and whether starters.json already
covers the gameweek. The first run inside the window does a deep pass:
15-25 searches on top of the ordinary ones, at least one per club, aimed
at predicted line-ups, the manager's press conference and the injury
lists, and writes data/starters.json with a verdict per player - start,
likely, doubt, bench or out - a one-line reason, the evidence, and the
articles. Later runs in the window amend a verdict only where the day's
news changes it, and runs outside the window do not touch the file.

The Starters tab renders it: counts across the top, the squad sorted from
start to out, the next fixture beside each name, and the model's own
probability that he plays as a pill, amber where it disagrees with the
verdict in either direction. That disagreement is the point of the page.
The model reads minutes; it cannot read a press conference. A player who
has joined the squad since the research ran is listed as not yet checked
rather than silently missing, and a player who has left is flagged.

The validator refuses the file unless every player in it is ours, every
one of ours is in it, statuses are from the fixed set, and each entry
carries a source unless the API already marks him out.

Below the verdicts sits a suggested XI that puts the two sources
together. The model's expected points for the gameweek already carry its
own availability estimate, so that is stripped back out to a
per-appearance rate and replaced with the chance he starts according to
the research: 0.95 for start, 0.75 likely, 0.5 doubt, 0.15 bench, 0 out.
A player the research has not covered keeps the model's number. The
eleven is then picked under the same formation rules as the My squad tab,
and the card names exactly who it moves in and out against the model's
own XI, so the disagreement is one line rather than two tables to compare.

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
