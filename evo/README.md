# An evolutionary neural network that plays the draft

A population of small neural networks plays six-manager FPL Draft
leagues against itself over the five archived seasons. The ones that win
breed. The survivor is then pointed at the live season, where it makes
the three decisions this game actually consists of: who to draft, who to
claim on waivers, and who to start.

Nothing here touches the existing pipeline. The matrix-completion model
still owns the dashboard, the projections and `data/claims.json`; this
writes `data/evo_plan.json` beside them so the two can be compared over a
season before either is trusted with the squad.

```
python -m evo.run features      # build the point-in-time feature cache
python -m evo.run selftest      # the leakage and legality checks
python -m evo.run cv    --out evo/runs/cv --kind loso
python -m evo.run report --out evo/runs/cv
python -m evo.run train --out evo/runs/final --all-seasons --generations <best>
python -m evo.run live  --model evo/runs/final/ckpt.npz
```

On a cluster: `evo/slurm/features.sbatch`, then `cv.sbatch` (a five-task
array, one fold each), then `train.sbatch`. Edit `evo/slurm/env.sh` for
your site; nothing else should need touching. Only numpy, pandas and
scipy are needed - the three `requirements.txt` already pins. There is no
GPU path and nothing to compile.

## The policy

One shared encoder, three heads:

```
h       = tanh(x W1 + b1)                 the player, as this genome sees him
score_k = [h, context_k] w_k + b_k        one head per decision
```

The three decisions share the encoder because they are the same
judgement - how good is this player, to me, now - asked over different
horizons, and sharing it is what lets ninety draft picks worth of signal
train the line-up head as well. The context is what the player features
cannot carry: for the draft, the round, the gap to the manager's next
pick, his remaining positional needs and the scarcity about to hit each
position; for a waiver, the gameweeks left, his strength at that
position, his league position and the gap to the leader.

The genome is 1,295 numbers - one flat float32 vector, which is what
makes evolution practical.

### Residual scoring

```
final = baseline + scale_k * unit_k * score_k
```

The baseline is the heuristic this repo already uses everywhere: a shrunk
points-per-match, an availability estimate from trailing minutes, and a
fixture multiplier. `unit_k` is the spread of that baseline over the
candidates, so `scale_k` is dimensionless, and a genome initialised near
zero plays the heuristic exactly.

This matters more than it looks. The reward is one season total arriving
after a hundred and thirty decisions; an evolutionary search that starts
from random play spends its whole budget rediscovering that a striker who
plays is better than one who does not. Starting from a competent manager,
every generation is spent on what improves him. `--no-residual` runs the
pure-network experiment for comparison.

### Why evolution

The action space is combinatorial - one pick out of six hundred, one
eleven out of fifteen - the reward is a single season total, and the
opponents move as the population learns. There is no differentiable path
from a weight to that reward. Evolution needs only the score, and it
parallelises across a cluster with nothing but `multiprocessing` and a
job array.

Truncation selection on the top quarter, elitism of five, uniform
crossover 30% of the time, and log-normal self-adaptation of each
genome's own mutation size.

### Self-play, and what stops it eating itself

Each league seats four genomes plus two outsiders: half the time a
heuristic manager, half the time a former champion from a hall of fame.
Without the heuristics a population drifts into beating only itself.
Without the hall of fame it cycles, rediscovering a strategy that beats
this generation and lost to the last one.

The six heuristics are the market follower, the last-season loyalist, the
form chaser, the points-per-game manager, the baseline, and a noisy
version of the baseline. None of them is ever in the population, so
validation against them is a genuine out-of-sample test of the policy.

Fitness is shaped rather than winner-take-all, because one season in a
six-team league is far too noisy to rank two hundred genomes on titles:

```
1.0 * won  +  0.5 * (5 - rank)/5  +  0.5 * tanh(margin / 100)
```

## The data, and the rule the whole thing rests on

**A number attached to a decision taken at time t is computed from match
rows whose kick-off is strictly before t.**

The clock is a timestamp, not a gameweek index. A postponed fixture
breaks the gameweek ordering, and a gameweek-indexed window silently
reads the future when it does. Decisions happen at the waiver deadline -
a day before the gameweek deadline - and the line-up shares that
information set, which is the true clock for the waiver and conservative
rather than optimistic for the line-up.

`selftest.py` proves it rather than asserting it. Rebuild a season from
an archive with every match after gameweek k deleted; every feature
before the cut must come out bit-identical. On its first run that test
failed, and it was right twice:

- a player's club was read from his last row of the season, so a January
  transfer put him at a club nobody knew about yet in August;
- the positional prior for the shrunk mean averaged over the season's
  final roster, which is a small but real piece of knowledge about who
  was going to be signed.

Both are fixed. A second test permutes the *future* realized points and
replays with the same seed: every draft pick, roster and eleven before
the cut must be unchanged, and they must diverge after it - a test that
always passed would prove nothing.

### Injury history

FPL publishes a player's `status`, `chance_of_playing` and a line of
`news` - "Hamstring injury - 25% chance of playing" - but only ever for
right now, and the match archive carries none of it. `evo/injuries.py`
reconstructs the history from the one place it survives: the archive
repository commits `players_raw.csv` once a gameweek, and that file *does*
carry all four fields, so its git log is a point-in-time injury feed.

```
python -m evo.injuries --repo /path/to/Fantasy-Premier-League
```

writes `data/injuries_{season}.csv`, a change log rather than a panel -
about 3,500 state changes a season across 800 players, a couple of
hundred kilobytes each.

The snapshots are weekly, but the resolution is better than that, because
FPL stamps every item with `news_added`: the moment it was published. A
note posted at 09:30 on the Friday was visible to every manager in the
game from 09:30 on the Friday, so dating the state from there is a fact
about what was knowable rather than a peek forward. Three quarters of the
rows are back-dated this way, by a median of about a week.

The reverse inference is not sound and is not made. A snapshot taken
after time t showing a player fit says nothing about whether he was fit
at t, so a state is only ever read forward from its own start. That
distinction is not cosmetic: the first version back-dated *clearings*
too, using the `news_added` of the injury that had just ended, which had
Tomiyasu fit from the day he got hurt. `selftest` truncates this log
along with the match archive, so a violation fails a test rather than
quietly improving a backtest.

**It is worth about +34 points a season**, measured on the heuristic
manager alone - the same policy, the same seeds, with the log and
without:

```
   season   with injuries   without     diff
  2021-22            1526      1464      +62
  2022-23            1664      1652      +12
  2023-24            1669      1632      +37
  2024-25            1706      1691      +16
  2025-26            1705      1661      +44
```

Positive in all five. That is a larger edge than anything the network
found on its own in the dry run below, which is worth sitting with: the
data was missing, not the model. `--no-use-injuries` reruns the ablation.

Two honest gaps. The cadence is a gameweek, so a knock picked up and
cleared inside one week can be missed entirely. And the harvest stops
wherever the archive repo last committed, which during a live season is
days behind - `python -m evo.injuries --live` appends the draft API's
current state, and the live driver does it automatically when online, so
one call a day keeps the log current and next season is already
harvested rather than needing reconstruction.

### Features

Fifty-five per player per gameweek, all as at the decision:

- trailing points per appearance, minutes share and start share over the
  club's last 3, 6, 12 and 38 matches - measured in the *club's* matches,
  so a player who has lost his place is charged for the games he missed
  rather than flattered by the last one he started;
- xG and xA per 90, BPS, bonus, clean sheets, saves, defensive
  contribution;
- last season's points per appearance and appearances, with an indicator
  where there is no last season;
- the market: price and ownership as of his last row;
- the injury state: the advertised chance of playing, whether he is out
  or doubtful, how long he has been in that state, and how stale our last
  observation of him is;
- availability, and his club's rolling scored and conceded rates shrunk
  toward last season's (a promoted club gets the tails of that
  distribution);
- the coming fixture and the next five: how many, home share, and the
  opponents' rolling rates;
- the three heuristic baselines themselves.

Player identity is never a feature, so the network cannot memorise who
hauled in 2023.

### Assumptions, stated rather than buried

**Membership is known; performance never is.** To simulate a season you
have to know who was on a Premier League roster. The archive carries a
row for every *registered* player in every gameweek, so a player's first
row dates his registration - which was public at the time - and the pool
is built from that. A January signing becomes available the week he is
registered, not before. Nothing about how he does that season, that
season's final totals, or the end-of-season snapshot files ever reaches a
feature.

**The fixture list is known in advance**, because it is. Results from it
never are: only the gameweek, the two clubs and the kick-off time.

**The pre-season market is not used by default.** A player's opening
price is published before a ball is kicked, but opening ownership is not
published before a typical draft, so `preseason_market` is off and the
draft board is built from football alone. Turn it on to trade a little
honesty for a lot of signal.

**Bookmaker odds are implemented but off.** `data/odds_*.csv` covers the
five archive seasons and not the live one, and a feature the live model
cannot compute is worse than no feature at all. Turn `use_odds` on once
`odds_2026-27.csv` exists.

**Injuries are inside the model, and are harvested rather than bought.**
See the next section. Availability at any decision carries the game's own
status and chance-of-playing as of that moment, in training exactly as
live.

**Defensive contribution exists only from 2025/26.** Earlier seasons
score zero there and carry an indicator, so the network can tell the
difference. It is scored under the live season's rules throughout, which
is the same choice `panel.py` makes.

**2021/22 has no xG or xA** in the archive, and carries an indicator for
that too. It is the weakest fold.

**Trades are out of scope.** Waivers are same-position swaps, which keeps
the 2/5/5/3 quota valid by construction.

**The candidate shortlist is a computational restriction, not a
modelling one.** Eighty players are scored per draft pick and sixty free
agents per waiver window, ranked by the shared heuristic board. A genome
cannot claim the 200th-best free agent even if it would have wanted to.

### Mechanics

The game's, not a convenient approximation of it. Snake draft, 15 rounds,
2/5/5/3 with forced fill. Waivers from gameweek 2, up to three ranked
claims each, processed in reverse-standings order, one success per
manager per week. Eleven starters, exactly one keeper, 3-5 at the back,
2-5 in midfield, 1-3 up front, no captain. Automatic substitutions
applied with realized minutes. The line-up and substitution rules are
imported from `schwaddy.lineup` and `schwaddy.draftsim` rather than
reimplemented, so the rules here and the rules on the dashboard cannot
drift apart.

## Cross-validation

Whole *seasons* are left out, because a season is the unit that has to
generalise: within one season the same players, clubs and scoring quirks
recur every week, so a random split of gameweeks would leak almost
everything.

- `loso` - five folds, each trained on four seasons and validated on the
  fifth.
- `forward` - trained on the four earliest, validated on the most recent.
  The honest forward test, and the only one whose direction of time
  matches how the model will be used.

In every fold the feature standardizer is fitted on the training seasons
alone, and the population and hall of fame never see the held-out season.

The validation metric is a **paired** difference. The same league is
played twice - identical season, opponents, seat and seed - once with the
genome in the seat and once with the baseline heuristic in it. Everything
except the policy is held fixed, so the difference is the policy's.
"Beats the baseline" therefore means the network found something the
shrunk-mean-times-availability manager did not, which is the only claim
worth making.

The generation count that maximises the mean validation score across
folds is the only thing the held-out seasons are allowed to decide; the
live model is then trained on every season for exactly that many
generations.

### What the dry run in this repo actually found

A small run - population 60, eight leagues per genome, 40 generations,
all five leave-one-season-out folds, 30 validation leagues per checkpoint
- is the pipeline's proof of life. Paired points a season against the
baseline manager, meaned over the five folds:

```
   gen  fitness    train    valid  +-fold  +-pair     gap  smoothed
     5    0.571    +19.4     -2.3    11.8     8.7   +21.7      +8.0
    10    0.538    +21.4    +18.3    12.3     7.8    +3.1      +4.3
    15    0.565    +23.6     -3.2    23.5     8.7   +26.8     +10.2
    20    0.595    +53.9    +15.3    10.8     8.5   +38.5      +2.8
    25    0.601    +54.7     -3.8    21.2     8.8   +58.5      -3.1
    30    0.596    +71.1    -20.9     9.5     9.9   +92.0     -10.8
    35    0.626    +65.8     -7.7    27.5     9.5   +73.5     -12.5
    40    0.604    +88.1     -9.0    15.0     9.8   +97.1      -8.3
```

Read it and do not flinch: the network learns the seasons it trains on -
+19 points a season by generation 5, +88 by generation 40 - and on the
held-out season it does not beat the baseline at any generation. Win rate
against the same six heuristics tells the same story: 0.47 against the
baseline's 0.38 at generation 10, back to 0.38 by 15, and 0.25 by 30. The
gap between the two columns is a clean, monotone overfitting curve, which
is what a cross-validation harness is for.

**So at this size the answer is no, and the honest thing is to say so
before it is run at scale.** What this run does establish is that the
machinery works end to end and that the measurement is sharp enough to
show the failure: the paired standard error is about ±9 points within a
fold and ±12 across them, so a real edge of thirty points a season could
not hide in it.

The levers against overfitting, in the order worth pulling. More leagues
per genome first: fitness noise is what selection overfits before it
overfits anything about football, and eight leagues over four seasons is
two seasons each. Then population, which at 60 is a third of the cluster
configuration. Then `l2`, the complexity penalty on the genome, which is
off by default and should be chosen by the same validation curve as
everything else - as should `hidden`, which at 24 may simply be more
network than 130 decisions a season can pay for.

If a full run comes back with the same shape, the finding is that a
1,295-parameter policy cannot be fitted from five seasons of six-manager
leagues, and the residual design means the fallback is not a broken model
but the heuristic it started from.

### On how much noise there is

A draft league is a small-sample machine: two managers with the same
policy in different seats can finish two hundred points apart. Pairing
removes most of that - the same season, opponents, seat and seed on both
sides - and what is left is the draft diverging, which is real. Measured,
30 paired leagues give about ±9 points a season within a fold and ±12
across the five. The cluster configuration uses 120, which halves the
first. Anything reported without an error bar beside it should be read as
noise until it has one.

## Files

```
evo/config.py     every knob, in one dataclass that a checkpoint stores
evo/injuries.py   the injury history, harvested out of git and kept current
evo/features.py   the point-in-time feature tensors and their cache
evo/net.py        the policy: encoder, three heads, six heuristic anchors
evo/sim.py        one simulated season: draft, waivers, line-ups, subs
evo/evolve.py     the evolution loop, hall of fame, checkpointing
evo/evaluate.py   paired comparison against managers never trained against
evo/cv.py         folds, the validation curve, early stopping
evo/selftest.py   the leakage and legality checks
evo/live.py       the live season: reads the draft API, writes evo_plan.json
evo/run.py        the command line
evo/slurm/        job scripts; edit env.sh and nothing else
```

Checkpoints are written every generation and `--resume` picks them up, so
a pre-empted or timed-out array task can simply be requeued.

## Using it this season

The draft happened on 21 August and three gameweeks are gone, so what is
live this year is the waiver and line-up heads from gameweek 4:

```
python -m evo.run live --model evo/runs/final/ckpt.npz
```

reads the draft API for the league's current ownership and my squad and
writes `data/evo_plan.json`: the eleven and the bench order, up to three
ranked waiver claims, and a draft board. `--offline` runs the same thing
from `data/league.json` and the cached bootstrap, which is what the dry
run in this repo does.

The draft head still trains and is still validated, because it shapes the
squads the other two heads inherit in every simulated season. It comes
into its own next August.
