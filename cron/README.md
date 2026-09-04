# Punctual scheduling

GitHub's `schedule:` crons are queued on shared runners and run when
GitHub gets round to them. Measured over twelve consecutive scheduled
runs of `update.yml` (1-4 September 2026):

| | minutes late |
|---|---|
| median | 152 |
| worst | 305 |
| best | 14 |
| within 5 minutes of the slot | 0 of 12 |

The 13:45 UTC slot did not fire at all on any of those days: GitHub drops
a scheduled run rather than queueing it when the shared pool is busy. For
a dashboard whose job is to be right before a waiver deadline, a refresh
that lands two and a half hours late, or not at all, is the difference
between a decision and a post-mortem.

A workflow started through the API is not queued that way; it begins
within seconds. So this worker keeps the schedule and presses the button,
and GitHub's own cron block stays only as a backstop.

`worker.js` runs on Cloudflare Cron Triggers, which fire on time and cost
nothing on the free plan. On each slot it asks GitHub when the workflow
last ran, skips if that was under 20 minutes ago (so a backstop cron or a
push that already did the work is not doubled), and otherwise dispatches
`update.yml` with the right `news_only` input, retrying three times.

## Deploy, once (about five minutes)

1. **Make the token.** GitHub → Settings → Developer settings → Personal
   access tokens → Fine-grained tokens → Generate new token.
   - Repository access: **Only select repositories** → `schwaddy-fpl`
   - Permissions → Repository permissions → **Actions: Read and write**
   - Nothing else. Copy the token.
2. **Deploy**, from a terminal with Node installed:

       cd cron
       npx wrangler login
       npx wrangler deploy
       npx wrangler secret put GH_TOKEN     # paste the token

   `wrangler deploy` reads `wrangler.toml`, so the five cron triggers are
   set up with it. Or in the dashboard: Workers & Pages → Create Worker →
   name it `schwaddy-cron` → paste `worker.js` → Deploy, then Settings →
   Variables and Secrets → add `GH_TOKEN` as a **Secret**, then Settings →
   Trigger Events → Cron Triggers → add the five expressions from
   `wrangler.toml`.
3. **Check it.** Open `https://schwaddy-cron.<you>.workers.dev/`. It
   should show `"token_configured": true` and the five slots. It makes no
   GitHub call, so it is safe to open and cannot leak the token.

## Checking it works

Runs it started show up in the Actions list with the event
`workflow_dispatch` rather than `schedule`, and their `created_at` should
be within a minute of the slot. Cloudflare's dashboard (the worker →
Logs) shows one line per firing: `dispatched`, `skip` with the age of the
last run, or a failure with the GitHub error.

Once a few days of that look right, the `schedule:` block in
`.github/workflows/update.yml` can be deleted and this becomes the only
trigger. Until then the two run side by side: the worker is punctual, the
GitHub cron is the safety net, and the 20-minute freshness check stops
them stacking two refits on each other.

## Changing the schedule

Edit `crons` in `wrangler.toml` **and** `SLOTS` in `worker.js` - the keys
must match exactly, since Cloudflare hands the matched expression to the
handler and the worker looks the slot up by it. A cron with no matching
slot logs `no slot for cron ...` and does nothing, which is the safe way
round. Then `npx wrangler deploy` again.

## The suggest endpoint

The same worker also answers `POST /suggest`, which is where the box on
the per-manager sites files a suggested headline or roast. It validates
the payload, caps the text, checks the sender and target are two of the
six managers, and dispatches `.github/workflows/suggest.yml`, which does
the committing. That needs no permission beyond the Actions write the
token already has.

The endpoint is public, like the sites. It checks the browser's `Origin`
header against the GitHub Pages and custom-domain origins, which turns
away everything casual without pretending to be a real boundary. If it
ever attracts nuisance traffic, the fix is a shared key in the body or a
Workers KV rate limit, neither of which is worth the setup today.

**After changing `worker.js` you must redeploy it**, either with
`npx wrangler deploy` from this folder or by pasting the file into the
dashboard editor and clicking Deploy. The cron triggers and the `GH_TOKEN`
secret survive a redeploy; only the code changes.
