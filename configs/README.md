# configs/ — Strategy Parameter Inputs, Not Trading Records

The JSON files in this directory are **experiment inputs**: parameter sets
(step sizes, close rules, rearm policies, symbol lists) captured from
tuning and research runs. They are not trade logs, account statements,
brokerage records, or profit claims.

## The `_live` / `_shadow` / `_deploy` filename suffixes

The suffix names the **lane the parameter set was intended for**, not a
record of what happened in that lane:

- `_live` — parameters shaped for a live-execution lane
- `_shadow` — parameters shaped for a shadow/paper lane
- `_deploy` — parameters shaped for a deployment candidate lane

A file named `*_live.json` does **not** mean those parameters were traded
live, made money, or are safe to trade. Nothing in this directory contains
broker credentials, account numbers, or real-money results.

This repository is paper/experimental research infrastructure. See the
[root README](../README.md) for the full framing and disclaimer.
