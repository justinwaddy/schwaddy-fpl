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

Fitness is **season points**. That is what an aggregate-scoring league
is won on, and it is what every paired measurement here reports, so the
objective and the measurement are the same number. A genome's total is
taken relative to the mean of every genome that played the same season
in the same round - every genome in a round plays the same season, so
that removes the season's level and nothing else. An earlier version
shaped it with a win bonus and a rank term; that rewarded the seat a
genome drew as much as the points it scored.

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

Does the flag mean anything? On 138,361 player-gameweeks, taking each
player's state as at that gameweek's decision:

```
   season   flagged out   P(play | out)   P(play | ok)   P(play | doubtful)
  2021-22         23.2%           0.022          0.512                0.311
  2022-23         25.9%           0.018          0.565                0.314
  2023-24         32.3%           0.022          0.557                0.352
  2024-25         26.9%           0.021          0.580                0.384
  2025-26         30.6%           0.056          0.530                0.448
```

A player the game had flagged plays about two per cent of the time; one
it had not, better than half; a doubtful, a third. The ordering holds in
every season. As a probability of playing, multiplying the trailing
minutes share by the advertised chance cuts the Brier score by 8%
(0.1398 to 0.1286).

2025/26 is the weak season - twelve snapshots rather than forty, so
states go stale and a fifth of its "out" flags are wrong. It is the
newest season and the one the live model leans on most, which is another
reason to keep the log current daily from here rather than harvesting it
after the fact.

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

**The return date is read.** "Calf injury - Expected back 13 Sep" is
FPL's own estimate, published with the item, and it is the one thing that
separates a player worth carrying on the bench from one worth dropping.
It is parsed (with "Suspended until", and a status-typical default where
no date is given - a knock is this round's question, an undated injury
is a long one, a loan is the season) and it changes the five-week
baseline: this round is whatever the game advertises, later rounds are
fit once the expected return has passed. A two-week injury is no longer
priced as a five-week one. Measured on 2024/25, a doubtful player's
five-week value is 1.3 times five-times-this-week rather than exactly
that, and a player who has left the club still values at nothing.

Two honest gaps. The cadence is a gameweek, so a knock picked up and
cleared inside one week can be missed entirely. And the harvest stops
wherever the archive repo last committed, which during a live season is
days behind - `python -m evo.injuries --live` appends the draft API's
current state, and the live driver does it automatically when online, so
one call a day keeps the log current and next season is already
harvested rather than needing reconstruction.

### The market

There is no budget in draft, so a price is not a cost here - it is
information, and it is the only input in the whole feature set that is
not derived from the same match data as everything else. FPL moves a
price on net transfers, so a price move is a crowd forecast of a player's
returns, revised nightly by several million people. The archive carries
`value`, `selected` and `transfers_balance` for every player in every
gameweek, which is that forecast as a point-in-time series, and the model
was previously reading only two levels off it.

Nine columns now: the price and ownership levels; the price move over one
and four gameweeks; the move since the player's first week of the season;
his price percentile WITHIN HIS POSITION, because a raw price is not
comparable across seasons - the game inflates - and "is he a premium" is
a statement about his position's market; the change in log ownership; and
net transfers over one and four gameweeks as a fraction of his ownership,
which is the flow the price algorithm actually responds to.

Measured as plain least squares on realized gameweek points, trained on
four seasons and scored on the fifth, the market block adds R² in every
one of the five:

```
   held-out   baseline only   + market      gain
    2021-22          0.3036     0.3165    +0.0130
    2022-23          0.3181     0.3269    +0.0088
    2023-24          0.3050     0.3152    +0.0102
    2024-25          0.3113     0.3212    +0.0099
    2025-26          0.2826     0.2944    +0.0118
```

About a 3.5% relative improvement, on 138,361 player-gameweeks. Unlike
the injury log this cannot help the heuristic baseline, which does not
read prices - it is a pure network input, so its value shows up in
cross-validation or not at all.

One split that matters for the draft. A player's OPENING price is
published weeks before any draft and is the game's own pre-season
expectation of him - for a summer signing with no Premier League history
it is the only read anybody has, so `preseason_price` is on. Opening
OWNERSHIP keeps moving right up to the first deadline, which is after a
typical draft, so `preseason_market` stays off.

### Features

Eighty-two per player per gameweek, on each of the week's two clocks,
all as at the decision:

- trailing points per appearance, minutes share and start share over the
  club's last 3, 6, 12 and 38 matches - measured in the *club's* matches,
  so a player who has lost his place is charged for the games he missed
  rather than flattered by the last one he started;
- xG and xA per 90, BPS, bonus, clean sheets, saves, defensive
  contribution;
- last season's points per appearance and appearances, with an indicator
  where there is no last season;
- the market, in nine columns rather than one (see below);
- the injury state: the advertised chance of playing, whether he is out
  or doubtful, how long he has been in that state, how stale our last
  observation of him is, when he is expected back and whether that date
  was published or defaulted, and how many of the next five gameweeks he
  is expected to miss;
- availability, and his club's rolling scored and conceded rates shrunk
  toward last season's (a promoted club gets the tails of that
  distribution);
- the coming fixture and the next five: how many, home share, and the
  opponents' rolling rates - venue-split, since home advantage is a tenth
  of a goal each way and a pooled rate hides it;
- the clean-sheet mechanics: Poisson expected clean sheets, expected 2+
  conceded and expected 2+ scored, this week and over five. A defender is
  paid on a step function of goals conceded, so the probability is the
  feature and not the rate behind it;
- the shape of the run: the position-aware fixture multiplier over one,
  two and five gameweeks, and near minus far;
- congestion, from kick-off times alone: rest before the next match and
  matches in the fortnight ahead;
- eight position x opponent columns, zeroed unless `pos_interact` is on.
  `panel.py` records that this exact idea lost realized points for the
  matrix model in every season tested, so it is a flag and a
  cross-validation question, not a default;
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

**The fixture list is known in advance - in two halves, at two times.**
Who a club plays and where is published in June. Which gameweek a match
lands in is settled a few weeks out, once cups and television have had
their say, and a blank or a double IS a rescheduling. The first version
here read blanks and doubles off the archive, i.e. off where the match
finally went, which had the model knowing in August that a club would
double in February - 61 blank and 61 double club-gameweeks in 2021/22
alone. `fixture_horizon` (three gameweeks) fixes it: inside the horizon
the schedule as played, beyond it the schedule as published, one fixture
a gameweek. `selftest` asserts both halves. Measured on the heuristic the
leak had been worth nothing - closing it scored five points a season
BETTER, a greedy five-week horizon over-reacting to a distant blank - so
this is a correctness change, not a number change.

**The pre-season market is not used by default.** A player's opening
price is published before a ball is kicked, but opening ownership is not
published before a typical draft, so `preseason_market` is off and the
draft board is built from football alone. Turn it on to trade a little
honesty for a lot of signal.

**Bookmaker odds are not used.** `data/odds_*.csv` covers the five
archive seasons and not the live one, and a feature the live model cannot
compute is worse than no feature at all. An earlier draft of this file
said they were "implemented but off"; they were not implemented, and the
flag has been removed rather than left as a promise.

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

**A released player goes on waivers, not into free agency.** "Players
released by other managers do not immediately become free agents.
Initially, they can only be signed through a waiver request" - the
Premier League's own guide. So a player dropped in waiver processing is
not in that afternoon's free-agent pool, and one dropped in free agency
is safe from everybody until next week's processing. The first version
here had every discard available to the next manager the same afternoon,
which made churn dearer than it is; it was flagged as unverified and
then verified, and the simulator now follows the rule. The same guide
says a successful claimant moves to the END of the waiver queue rather
than being done for the week, which the simulator also now does.

**The injury log can be optimistic about recoveries.** FPL can raise a
chance-of-playing without touching `news_added`, so a recovery first seen
at 75% may be dated from when it was posted at 25%. Undetectable from the
data, rare, and in the direction of overstating availability.

**The candidate shortlist is a computational restriction, not a
modelling one.** Eighty players are scored per draft pick and sixty free
agents per waiver window, ranked by the shared heuristic board. A genome
cannot claim the 200th-best free agent even if it would have wanted to.

### What fixtures are worth, measured the right way

A regression of realized points on features, scored per player-gameweek,
says the fixture columns add almost nothing once the baseline - which
already carries a fixture multiplier - is in. That was reported here
first, and it is the wrong instrument for the question. Fixtures act on
DECISIONS: which eleven to start, and above all which weak slot to churn
for whoever has the good game this week. The simulator measures that.
Paired leagues against the reference heuristic, three variants of it:

```
                      blind          stream           blend
  2021-22        -36.8 +- 19      +4.2 +- 19     +18.2 +- 19
  2022-23       -115.4 +- 18     -20.1 +- 18     +21.2 +- 17
  2023-24        -88.4 +- 14     -38.1 +- 19      -3.9 +- 16
  2024-25        -44.8 +- 11     +11.0 +- 11     +23.0 +- 12
  2025-26        -32.5 +- 13     -10.3 +- 15      +7.4 +- 13
  mean                -63.6           -10.7           +13.2
```

`blind` knows blanks but not difficulty: fixture knowledge is worth about
sixty points a season to the decisions, every season. `stream` churns
the weakest slot on this week's expected points alone and loses eleven -
churn still has a cost, though less than the thirty-four measured before
the release rule was corrected, when every discard could be snapped up
the same afternoon; under the real rule a discard is safe for a week.
`blend`, half this week and half the five-week run, beats the reference
by thirteen, and by twenty in three of the five seasons. So the mechanism is real, the naive version of it is a trap, and
the right horizon mix is a learnable thing - the network's waiver head
sees this week, the run, and the slope between them. `waiver_blend` puts
the blend into the heuristic itself; it defaults to zero so that the
benchmark stays where these numbers were measured.

### The week, and its two clocks

A gameweek asks a manager for three things at two different moments, and
the model keeps them apart because the game does:

```
  ... gameweek N deadline ......... waivers_time ......... gameweek N+1 deadline
                 |                       |                        |
                 |<--- waiver window --->|<-- free agency ------->|
                    write ranked claims      first come, first     team
                                             served                sheet
```

`waivers_time` is exactly 24 hours before the deadline. The league's own
transaction log settles it - every waiver claim was submitted before it
and processed at it, and the one free-agent signing landed at 14:14 on
the Friday, after waivers had run and three hours before the deadline:

```
  kind  gw  submitted         result   waivers_time      deadline
  w     3   2026-09-03T16:05  a        2026-09-03T17:30  2026-09-04T17:30
  w     3   2026-09-03T16:05  di       2026-09-03T17:30  2026-09-04T17:30
  f     3   2026-09-04T14:14  a        2026-09-03T17:30  2026-09-04T17:30
```

So the features are built on both clocks. Waiver claims are written on
the earlier one; free agency and the team sheet on the later one, which
is a day more team news - and with the injury log in, a day of team news
is not nothing - and comes after every rival's waivers have already
landed. `X` and `X_dl` in the cache are those two, and `selftest` proves
non-anticipation on each of them separately.

Free agency is scored by the same head as the waiver, with the window as
a context flag, so one head learns that the two are different problems
rather than two heads each learning half of one.

### Choosing how many, and what to carry

Two things the first version could not do, and the reason each matters.

**How many moves this week.** A claim used to be any pair that cleared a
fixed margin - one evolved number, the same every week - so the count of
claims was whatever happened to clear a bar, not a decision. Now the
margin is scaled by the squad's situation, `exp(w . context)`: gameweeks
left, league position, gap to the leader, how many of the fifteen blank
or double, and what the bench is carrying. "Hold when leading, churn when
chasing, wait when the bench is already full of passengers" are all
things a genome can express. The reference heuristic has one number and
no situation, which is the point of it.

That one number was re-chosen under the new mechanics before anything
was measured against it, because a reference that is allowed to chain
claims at the old margin of 0.25 over-churns - fifteen wins in a week -
and a weakened reference flatters the network for the wrong reason, the
trap the first review caught. The heuristic against itself, paired,
points a season over 0.25 with chaining:

```
              seq 0.5  seq 1.0  seq 1.5  seq 2.0  flat 0.25  flat 0.5  flat 1.0  flat 1.5  flat 2.0
  2021-22       -18.5    +15.5    +26.3    +18.2      +17.9     +19.5      +5.3     +48.2     +47.0
  2022-23        +3.4    -12.4    +18.1     +2.6       +5.0     -34.0      +4.4     -26.5     -12.3
  2023-24        -7.8     -7.1     -0.3    -14.4      -12.2      -2.0      +0.7     -12.5     -22.5
  2024-25       -10.3     +5.2     +8.2     +8.9       -1.4      -6.6      +3.8      -2.8      -6.0
  2025-26       -11.8    -14.5    -16.9    -11.8      -17.0      -3.6      +7.2      +5.6      -9.1
  mean           -9.0     -2.7     +7.1     +0.7       -1.5      -5.3      +4.3      +2.4      -0.6
```

`margin0` is 1.5 with chaining on: the best fixed-margin manager the
mechanics allow, which is what the genome starts from and what it is
scored against. The spread across seasons is wide - the most recent
season prefers a flat list at 1.0 - so this is a defensible choice, not
a sharp one.

And the list is built the way a careful manager builds one. Take the best
swap; add a couple of alternatives for the same drop in case a rival gets
there first; ASSUME it goes through; re-score the squad that results; go
again until nothing clears the bar. Every claim after the first is judged
against the squad the earlier ones leave. This is legal - claims are
submitted in advance and the game processes them in order, exactly as
written - and it is what turns "one change is enough this week" into
something the policy can say. `sequential_claims` switches it off, for
the flat single-scoring list.

**What to carry on the bench.** Four bench slots can carry passengers
for nothing this week; the question is who. The waiver head now sees, for
every candidate, whether the swap would change the ELEVEN or only the
bench - is the player it would drop a starter, would the player it would
add displace one - and how many expected points the bench is currently
carrying. With the return date in the features and the recovery in the
baseline, holding a good player through a short absence on a spare bench
slot is a visible, learnable choice rather than something the baseline
argues against every week.

### Mechanics

The game's, not a convenient approximation of it. Snake draft, 15 rounds,
2/5/5/3 with forced fill. Waivers from gameweek 2, unlimited - any
number of ranked claims, any number of successes, processed in
reverse-standings order with a successful claimant moving to the back of
the queue and the queue going round again; the switching margin is what
stops a manager churning his whole squad. Then the free-agency window: whoever gets there first
takes the player, modelled as a fresh random order each week, because who
actually gets there first is a fact about how often six people look at
their phones and no archive records it. Eleven starters, exactly one
keeper, 3-5 at the back, 2-5 in midfield, 1-3 up front, no captain.
Automatic substitutions applied with realized minutes. The line-up and
substitution rules are imported from `schwaddy.lineup` and
`schwaddy.draftsim` rather than reimplemented, so the rules here and the
rules on the dashboard cannot drift apart.

Two honest deviations. `fa_max_moves` defaults to one a week and the real
game has no limit, which is conservative rather than generous. And the
simulated managers churn far more than yours do. With waivers unlimited,
the heuristic manager makes 1.3 to 2.5 waiver moves per manager per week
across the five seasons, and up to eleven in a single week, against the
0.56 your league has averaged - a margin rule has no reluctance, and no
Saturday job. The network's margin gene is its own to set. Whether a
policy that churns three times a week is one you would actually run is a
question about you rather than about the model; `margin0` and
`max_success_per_gw` are the two knobs if the answer is no. Measured on the baseline manager the window is worth
about +25 points a season, but that is +105, +5, +23, -8, 0 across the
five: one season carrying it, and one slightly negative. It is in for
fidelity to the rules; whether it is worth anything is for the network to
find out.

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
- on the full build: season points as the objective, the whole season's
fixtures, return dates and the bench-aware waiver head, a situational
margin and sequential claim lists, waivers unlimited, the release rule
as the game plays it, and the reference re-chosen at its best margin.
Paired points a season against that reference, meaned over the five
folds:

```
   gen    train    valid  +-fold  +-pair     gap  smoothed
     5    +14.4    +18.6    11.9     6.6    -4.2      +7.0
    10    +54.8     -4.5    14.5     6.9   +59.3     +12.8
    15    +64.0    +24.4    27.4     7.5   +39.5     +25.5
    20    +80.4    +56.7    26.7     8.0   +23.7     +38.9
    25   +102.2    +35.6    17.1     7.9   +66.5     +54.0
    30   +129.1    +69.7    41.7     7.6   +59.4     +61.1
    35   +143.2    +78.0    34.5     8.6   +65.2     +89.2
    40   +152.0   +119.9    37.4     8.9   +32.0     +99.0

   mean over all checkpoints  +49.8 +- 14.0
```

**The number to believe is +50 points a season over the manager it
starts from, on seasons it has never seen** - and, for the first time,
the curve had not turned when the run ran out. From generation 15 the
held-out score rises through +24, +57, +36, +70, +78, +120, and the
stopping rule picked the last checkpoint because there was no later one.
Where it actually peaks is a question for the cluster's 200 generations.

Two things to hold against it. The spread across folds is wide - 12 to
42 points - so the season matters a great deal and a single season's
edge could be half or double the mean. And this is measured against a
reference that was STRENGTHENED before the run (the margin sweep above),
not weakened; the earlier lesson was that a weak reference flatters the
network, and this is the opposite direction.

Why it moved from +7.5. The previous run had unlimited waivers and a
reference that could use them, and the network had nothing the reference
did not: the same five-week baseline, a fixed margin, one-shot claim
lists, no return dates, no view past the week. Every piece since is
something the reference cannot do - price a two-week injury as two weeks,
see whether a swap changes the eleven or the bench, set its margin by the
situation, build a claim list against the squad the earlier claims leave,
see the run to May - or something that made the objective the right one.
Season points, not a shaped win score, is what it now maximises, and
season points is what this table counts.

The history, each step a commit: +31 reported; +18 of it a benchmark
artefact; +24 fair; +33 under the game's release rule; +7.5 under
unlimited waivers against a reference that could use them; +50 with the
objective and the horizon fixed.

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
evo/slurm/        job scripts and RUNBOOK.md; edit env.sh and nothing else
```

Checkpoints are written every generation and `--resume` picks them up, so
a pre-empted or timed-out array task can simply be requeued.

## From the cluster to Saturday

The point of training on the cluster is to use the result here, on the
live league, before a deadline. The round trip is three files and a
rule.

**The rule: a checkpoint is tied to the commit that made it.** It holds
1,300-odd weights laid out against a particular feature set and context
size, and `Brain` refuses to load one that does not match (it says so
rather than reshaping). So run `live` from the same commit the cluster
trained on. `git rev-parse HEAD` on the cluster before you submit; check
out that revision here before you run.

**On the cluster:**

```
sbatch evo/slurm/features.sbatch                 # cache + selftest, once
sbatch --dependency=afterok:<id> evo/slurm/cv.sbatch    # five folds
python -m evo.run report --out evo/runs/cv       # when all five are in
GENS=<the generation it prints> sbatch evo/slurm/train.sbatch
```

`train.sbatch` writes `evo/runs/final/ckpt.npz`. That one file - about
100 KB - is the model: the best genome, plus the feature standardizer it
was trained with. `evo/runs/` is gitignored, so bring it back by hand:

```
scp cluster:path/to/schwaddy-fpl/evo/runs/final/ckpt.npz evo/runs/final/
```

**Here, before the deadline:**

```
python -m evo.run live --model evo/runs/final/ckpt.npz
```

Online, it pulls the draft API for the league's current ownership and
your squad, brings the injury log up to today from the bootstrap, works
out which window the week is in, and writes `data/evo_plan.json`: the
eleven and the bench order, up to three moves - waiver claims to submit
before `waivers_time`, or free agents to take first-come-first-served
after it - and a draft board. It prints the same. `--offline` runs from
the repo's committed data files instead, which are refreshed by the cron
each morning; it will warn if the injury log is more than three days
old. `--gw N` overrides which gameweek.

The plan is written beside the existing model's output, not in place of
it. Commit it if you want it on the site; nothing reads it otherwise.

**Two things to keep current.** The injury log is appended every time
`live` runs online, and `python -m evo.injuries --live` does the same on
its own; once a day is enough, and it means next season is already
harvested. And when the archive repo publishes a season's final files,
`python -m evo.run features --rebuild` rebuilds the cache; the cluster's
`features.sbatch` does that too.

## Using it this season

The draft happened on 21 August and three gameweeks are gone, so what is
live this year is the waiver and line-up heads from gameweek 4:

```
python -m evo.run live --model evo/runs/final/ckpt.npz
```

reads the draft API for the league's current ownership and my squad and
writes `data/evo_plan.json`: the eleven and the bench order, up to three
moves, and a draft board. It works out from the bootstrap which window
the week is in and says so - before `waivers_time` the moves are ranked
waiver claims to submit, between then and the deadline they are free
agents to take first-come-first-served, and after the deadline the team
sheet is locked and the claims are for next week. `--offline` runs the same thing
from `data/league.json` and the cached bootstrap, which is what the dry
run in this repo does.

The draft head still trains and is still validated, because it shapes the
squads the other two heads inherit in every simulated season. It comes
into its own next August.
