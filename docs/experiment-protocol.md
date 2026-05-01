# Experiment Protocol

## Rule

One meaningful change per experiment.

If we change entry logic, sizing, exit logic, and symbol universe at the same time, we learn almost nothing.

## Graduation Ladder

Use the same language every time:

- `tested_theory`: the mechanism is coherent, the family is named, the benchmark to beat is named, and the shadow harness exists
- `shadow`: the idea is running forward on an honest current control path
- `validated_shadow`: the forward path stayed positive with acceptable reset/floating behavior and beat the family-local control or baseline
- `live`: the planned runtime path matches the validated shadow path and no contradiction or guardrail blocker remains

Do not skip stages because a shelf report or one hot sample looks exciting.

## Anti-Confusion Rules

- `family_firewall`: proof does not transfer across families, timeframes, or runtime paths
- `control_before_variant`: restore or normalize the active control before testing a new variant
- `bucketed_truth`: when the thesis is "less losses", split harvest, offensive-close, and forced-unwind buckets before judging the mechanism
- `spread_before_story`: if the shape is below the current spread-safe floor, it is not honest current proof
- `stale_runtime_firewall`: stale heartbeats, geometry drift, or mixed control truth invalidate promotion claims
- `single_changed_variable`: each experiment must say exactly what changed and what benchmark it is trying to beat

## Required Experiment Record

Before a run, write down:

- experiment id
- date
- canonical bot file
- account context
- stage: `tested_theory`, `shadow`, `validated_shadow`, or `live`
- evidence family
- hypothesis
- benchmark to beat
- exact change from previous run
- single changed variable
- symbols or universe in scope
- session window
- risk budget
- kill switch or reset condition
- proof path or runtime harness

After a run, record:

- runtime duration
- starting balance
- ending balance
- peak balance
- peak multiple
- max drawdown
- trade count
- win rate
- average winner / average loser
- best symbols
- worst symbols
- failure mode
- verdict

## Allowed Verdicts

- `promote`: keep and build on it
- `revise`: promising but unclear
- `reject`: do not compound on this
- `safety-fix`: not a strategy result, just a production repair

## Experiment Types

Use one primary tag per run:

- `entry`: signal or setup logic
- `exit`: stop, target, trail, time exit, cut logic
- `sizing`: leverage, pyramiding, equity scaling
- `universe`: which symbols can be traded
- `regime`: session filter, volatility filter, trend filter
- `execution`: spread handling, order fill behavior, crash resilience

## Promotion Gate

An experiment should only be promoted if:

- the result is better on the named family-local benchmark
- the mechanism makes sense, not just the outcome
- the risk of account death did not increase blindly
- the runtime path used for proof matches the promotion story
- the run can be reproduced from the notes

## Stage Gates

### Tested Theory -> Shadow

- mechanism is coherent
- benchmark to beat is explicit
- one variable changes
- kill condition is written before launch
- shadow harness is concrete

### Shadow -> Validated Shadow

- fresh forward-positive evidence exists on the current runtime path
- reset, spread, and floating-loss behavior stay inside acceptable bounds
- the candidate beats the family-local control or baseline on both profit quality and loss containment
- no contradiction, guardrail, or stale-runtime blocker is still open

### Validated Shadow -> Live

- planned live runtime path matches the validated shadow path
- the candidate survives at least one adverse regime segment
- governance and operator guardrails are aligned
- promotion is based on current forward truth, not copied shelf optimism

## Reset Discipline

Reset the account or start a fresh run when:

- the bot drifts from the logged configuration
- emergency manual intervention changes the strategy outcome
- account state is polluted by legacy positions
- the run no longer maps cleanly to one hypothesis

## Daily Rhythm

Use this rhythm to stay rigorous:

1. choose the one thing we are testing
2. run it long enough to learn something
3. log the result immediately
4. decide promote, revise, reject, or reset
5. only then touch the code again
