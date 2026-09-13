# Live scores

The Live tab on the dashboard polls FPL's live feed every 15 seconds while
a match is on and shows each manager's gameweek score as it happens, with
provisional bonus and provisional subs applied, plus every fixture and
who in the league has players in it. Between matches it does not poll at
all: it sleeps until the next kick-off, and stops once the gameweek is
over. The status line under the gameweek heading says which it is doing.

GitHub Pages cannot do this on its own: FPL's API sends no CORS headers, so
a browser on a different origin is refused. `worker.js` is a tiny
Cloudflare Worker that reads the API server-side, trims the feed to the six
squads and hands the page one bundled JSON. It caches for 15 seconds, so FPL
sees one round of requests per 15s no matter how many tabs are open, and
each viewer costs one request per poll against the free tier's 100,000 a
day. The page polls at whatever cache length the deployed worker reports,
so polling faster than the worker can answer never happens. Nothing in it
is secret: the API is public and reads need no login.

Neither the model nor the cron are involved. Predictions and the news feed
keep coming from the four daily refreshes; only the scoreboard is live.

## Who is watching

The top of the Live tab shows the initials of every manager whose page is
polling the worker: E, SB, BB, J, M, R, your own outlined in blue. Each
manager's site sends its owner's entry id with every poll (`?me=372099`),
the worker remembers who it heard from in the last 90 seconds, and hands
the list back on an `X-Online` header, so the cached snapshot body is
untouched and the query string does not split the cache. A page that is
hidden or closed stops polling and drops off within a minute or so;
between matches pages sleep until the next kick-off, so the row is only
telling you something while a match is on. Nothing else is recorded.

The list lives in a Durable Object, one for the league, so everyone sees
the same one wherever they are. That needs the binding in `wrangler.toml`,
which the dashboard's paste-and-deploy editor does not set up: deployed
that way the worker falls back to the Cache API, which is one copy per
Cloudflare data centre, so you see the others on your data centre and not
the rest. Deploy with wrangler (below) for the real thing.

## Deploy

The worker deploys itself. It is connected to this repository in the
Cloudflare dashboard (Workers & Pages -> schwaddy-live -> Settings ->
Builds): every push to `main` that touches `live/` runs
`npx wrangler deploy` on Cloudflare's side, with `live` as the root
directory, so `wrangler.toml` is read and the Durable Object binding
comes with it. Nothing to install and nothing to paste. Progress shows
under the worker's Deployments tab; a deploy takes about a minute.

The worker's URL is `https://schwaddy-live.<you>.workers.dev`, and it is
pasted into `LIVE_URL` near the top of `site/team.js`. Opening it in a
browser should show JSON starting `{"gw":`.

If the connection is ever lost, the fallback is the dashboard editor:
Edit code -> paste `worker.js` -> Deploy. That updates the code but not
the binding, so who-is-watching drops to the per-data-centre version
until a git deploy runs again. Or, with Node installed:

    npx wrangler login
    cd live && npx wrangler deploy

## What it returns

    GET /            (or /snapshot)

    { gw, finished, fetched, ttl,
      teams:    { id: "ARS", ... },
      rules:    { play, min_GKP, max_GKP, ... },      # from FPL's own settings
      fixtures: [ { id, h, a, hs, as, started, fin, min, ko, bonus_in, bps:[[element, bps], ...] } ],
      elements: { id: { n, t, p, pts, min, bonus, bps, fx:[fixture ids] } },   # owned players only
      managers: [ { entry, name, team, rank, total, event_total, prior, picks:[[element, slot], ...] } ] }

Every reply also carries `X-Online: 372099,45811,...`, the entry ids of
managers whose pages have polled in the last 90 seconds (see above).

`gw` is the gameweek in play, or the one coming: once FPL closes a
gameweek the worker rolls to the next one straight away, so on a Friday
the tab shows Saturday's fixtures and who has players in each. Until the
deadline the picks endpoint is closed, so rosters come from the league's
ownership list (waivers included) in last week's slot order, with
newcomers on the end; the real line-ups take over at the deadline.

`rank`, `total` and `event_total` are the game's standings, which FPL
re-tallies only when it closes the gameweek - on a Sunday night they sit a
whole day behind. `prior` is his cumulative total at the last closed
gameweek, which is settled; the page shows season = prior + its own live
gameweek, and only falls back to `total` if `prior` is missing.

The page does the arithmetic. Provisional bonus is the standard 3/2/1 on
each fixture's bps table with FPL's tie rules, applied only until the
official bonus lands; provisional subs follow the same formation rules as
`weekly.py`, so the live tab and the cron's league table agree once a
gameweek settles.

## Limits worth knowing

- FPL's own feed updates roughly every minute or two during a match, so
  most 15s polls come back unchanged; the cost of that is one small
  cached request, which is why it is set that low and no lower.
- Cloudflare's free tier: 100,000 requests a day. Six people watching a
  full Saturday at one request per 15s is about 20,000, and nothing at
  all between matches.
- If the worker is down the tab falls back to the cron's league.json and
  says so; the rest of the dashboard is unaffected.
