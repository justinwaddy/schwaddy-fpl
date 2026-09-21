# What runs on its own, and when

Nothing in this repo is updated by hand on a normal week. Five scheduled
things keep it current, in two families: GitHub Actions, which owns the
data and the models, and claude.ai Routines, which own the writing. This
file is the inventory, so that "is there a routine for that?" has an
answer that does not involve opening the Cloudflare dashboard.

All times UTC. The UK is +1 during BST.

## The data and the neural network

| what | when | how |
|---|---|---|
| full refresh: pull, refit, predictions, **both neural network plans** | `35 8 * * *` | `.github/workflows/update.yml` |
| news feed only | `45 13`, `0 17`, `20 22` daily | same workflow, `--news-only` |
| owner sync after waivers process | `45 17 * * 4` | same workflow |

The morning slot is the one that matters here. It runs
`schwaddy.refresh`, which pulls the classic and draft APIs and rewrites
`data/gws_<season>.csv` for every gameweek the archive repo has not
published yet - so the season's match log is current to the last
finished gameweek within a few hours of it finishing. Then it runs
`evo.run live` twice against the five committed seeds, writing
`data/evo_plan.json` (claims judged on the next five gameweeks) and
`data/evo_plan_rest.json` (the same claims judged on the rest of the
season). Both steps are guarded with `|| echo`, so a failure in the
network's plan can never cost the refresh.

**That is the whole "update the NN with the new gameweek" loop, and it
is already automatic.** The network reads the live season as features at
inference time; it is not retrained in-season. Retraining is a cluster
job (`evo/slurm/`, and `evo/README.md` for the cross-validation behind
it), it happens once between seasons, and a checkpoint is tied to the
commit that made it - so a new `data/evo_models/seed*.npz` must be
merged together with the code that trained it or the cron will load one
without the other. `data/evo_model.npz` is the pre-audit single model,
kept for the record and no longer run.

The schedules above are a **backstop**. The thing that actually fires on
time is the Cloudflare worker in `cron/`, which dispatches `update.yml`
through the API within seconds of the slot and skips if a run started in
the last twenty minutes; GitHub's own `schedule:` block was a median of
152 minutes late over twelve consecutive runs and dropped the 13:45 slot
entirely on every one of those days. `cron/README.md` has the numbers and
the deploy steps. A push to `src/`, `evo/`, the model files or the
workflows also triggers a full refresh, so a code change is validated
against real data immediately rather than at the next slot.

## The writing

Three Routines, created in the claude.ai Routines UI against this
repository, each firing a fresh session. They cannot be created from
inside a Claude Code session: `create_trigger` there stores no repository
and no tools, so the Routine fires into an empty container and dies at
step 0.

| Routine | when | writes |
|---|---|---|
| Schwaddy FPL headlines + claims + weekly starters | `0 8,21 * * *` | `data/claims.json`, `data/starters.json`, `data/editorial.json` |
| 27 Richmond Road Cup: league news and the evening wrap | `40 7,22 * * *` | `data/league_news.json` |
| Richmond Road Cup line-up report | `0 11-19 * * 5,6,0,1` | `data/league_news.json` |

The first of those is the waiver board: it reads the day's football
news, weighs it against the model's projections and the network's plan,
and writes up to five ranked add/drop pairs with reasoning and sources to
`data/claims.json`, which is the card on the justino page's Waivers tab.
Only that session writes the file, so it cannot collide with the refresh
cron. Its prompt lives in the Routines UI rather than here. The other two
write the page all six managers read, and their prompts are
`league_news.md` and `lineups.md` beside this file - both of which forbid
quoting anything from the model, so the research those Routines do stays
out of the public page.
