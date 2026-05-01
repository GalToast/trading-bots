# Scripts Guide

This directory contains research utilities, runners, diagnostics, and proof-board builders. It is intentionally broad, but the public review path is narrow.

## First-Read Files

| File | Why it matters |
| --- | --- |
| [`test_live_kraken_spot_frontier_maker_machinegun_shadow.py`](./test_live_kraken_spot_frontier_maker_machinegun_shadow.py) | Focused unit suite used by GitHub Actions. |
| [`live_kraken_spot_frontier_maker_machinegun_shadow.py`](./live_kraken_spot_frontier_maker_machinegun_shadow.py) | Runner covered by the focused test suite. |
| [`kraken_spot_client.py`](./kraken_spot_client.py) | Exchange client wrapper used by Kraken spot workflows. |
| [`live_penetration_lattice_shadow.py`](./live_penetration_lattice_shadow.py) | Shared shadow-runner support surface. |
| [`process_singleton.py`](./process_singleton.py) | Runtime guard for avoiding duplicate runner processes. |
| [`benchmarks/`](./benchmarks/) | Benchmark harnesses moved out of the public repo root. |

## How to Read This Directory

- Treat `test_*` files as behavior specifications or regression probes.
- Treat `build_*`, `watch_*`, and `validate_*` files as evidence and operator-surface generators.
- Treat live or broker-connected scripts as local-only workflows unless explicitly run in dry-run or shadow mode.
- Do not expect every historical research script to be a polished command-line product.

For the quickest technical review, start with the focused test and runner pair above, then compare their behavior to the proof snapshot in [`../docs/evidence/edge_registry.md`](../docs/evidence/edge_registry.md).
