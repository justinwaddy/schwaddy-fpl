# Live scores

The Live tab on the dashboard polls FPL's live feed every 30 seconds while
a match is on and shows each manager's gameweek score as it happens, with
provisional bonus and provisional subs applied, plus every fixture and
who in the league has players in it.

GitHub Pages cannot do this on its own: FPL's API sends no CORS headers, so
a browser on a different origin is refused. `worker.js` is a tiny
Cloudflare Worker that reads the API server-side, trims the feed to the six
squads and hands the page one bundled JSON. It caches for 30 seconds, so FPL
sees one round of requests per 30s no matter how many tabs are open, and
each viewer costs one request per poll against the free tier's 100,000 a
day. Nothing in it is secret: the API is public and reads need no login.

Neither the model nor the cron are involved. Predictions and the news feed
keep coming from the four daily refreshes; only the scoreboard is live.

## Deploy, once (about five minutes)

1. Make a free Cloudflare account at https://dash.cloudflare.com/sign-up
   (no card needed for Workers).
2. Workers & Pages -> Create -> Create Worker. Name it `schwaddy-live`
   (the name becomes the URL), press Deploy to accept the hello-world.
3. Edit code -> replace everything with the contents of `worker.js` ->
   Deploy.
4. Copy the worker's URL, e.g. `https://schwaddy-live.<you>.workers.dev`,
   and open it in a browser: you should see JSON starting `{"gw":`.
5. Paste that URL into `LIVE_URL` near the top of the script in
   `site/index.html`, commit, push. The pages workflow redeploys the site.

Or from a terminal, with Node installed:

    npx wrangler login
    npx wrangler deploy live/worker.js --name schwaddy-live --compatibility-date 2026-09-01

## What it returns

    GET /            (or /snapshot)

    { gw, finished, fetched, ttl,
      teams:    { id: "ARS", ... },
      rules:    { play, min_GKP, max_GKP, ... },      # from FPL's own settings
      fixtures: [ { id, h, a, hs, as, started, fin, min, ko, bonus_in, bps:[[element, bps], ...] } ],
      elements: { id: { n, t, p, pts, min, bonus, bps, fx:[fixture ids] } },   # owned players only
      managers: [ { entry, name, team, rank, total, event_total, picks:[[element, slot], ...] } ] }

The page does the arithmetic. Provisional bonus is the standard 3/2/1 on
each fixture's bps table with FPL's tie rules, applied only until the
official bonus lands; provisional subs follow the same formation rules as
`weekly.py`, so the live tab and the cron's league table agree once a
gameweek settles.

## Limits worth knowing

- FPL's own feed updates roughly every minute or two during a match. Polling
  faster than 30s buys nothing.
- Cloudflare's free tier: 100,000 requests a day. Six people watching a
  full Saturday at one request per 30s is about 10,000.
- If the worker is down the tab falls back to the cron's league.json and
  says so; the rest of the dashboard is unaffected.
