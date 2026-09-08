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

### Features

Forty-nine per player per gameweek, all as at the decision:

- trailing points per appearance, minutes share and start share over the
  club's last 3, 6, 12 and 38 matches - measured in the *club's* matches,
  so a player who has lost his place is charged for the games he missed
  rather than flattered by the last one he started;
- xG and xA per 90, BPS, bonus, clean sheets, saves, defensive
  contribution;
- last season's points per appearance and appearances, with an indicator
  where there is no last season;
- the market: price and ownership as of his last row;
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

**Injuries are applied outside the network.** The archive holds no
history of status flags, so a model cannot learn what it has never seen.
The live driver reads the API's status and chance-of-playing and applies
them as a multiplier after scoring. In simulation there are no injury
flags at all, which makes the simulated managers slightly better than a
real one at picking a man who is about to be ruled out.

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
all five folds - is committed as the pipeline's proof of life, and it is
worth reading for its shape rather than its numbers. The evolved genome
beats the baseline manager on the seasons it trained on by twenty to
seventy points a season, and on the held-out season it does not reliably
beat it at all. That is textbook overfitting, and it is exactly what the
gap column was added to show.

Nothing about that is a reason to trust the numbers less; it is a
population two hundred short of the one the cluster config uses, judged
on thirty validation leagues where the standard error is +-25. The levers
against it, in the order worth pulling: more leagues per genome (fitness
noise is what selection overfits first), a larger population, and `l2`,
the complexity penalty on the genome, which is off by default and should
be chosen by the same validation curve as everything else.

### On how much noise there is

A draft league is a small-sample machine. Two managers with the same
policy and different seats can finish two hundred points apart, and a
paired difference over 30 leagues has a standard error of about ±25
points a season - wider than most edges worth having. The cluster
configuration uses 120 validation leagues per checkpoint, which brings
that to about ±12, and the five folds average to about ±6. Anything
reported here without an error bar beside it should be read as noise
until it has one.

## Files

```
evo/config.py     every knob, in one dataclass that a checkpoint stores
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
