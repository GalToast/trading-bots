# Trading Bots Command Center

**Public snapshot date:** 2026-05-01
**Status:** Research and supervision infrastructure snapshot
**Deployment posture:** Public repo is evidence and review material only; broker-connected runtime state and credentials are excluded.

This file is a recruiter-readable command-center summary for the public repository. It describes how the system is organized and what can be inspected without implying financial advice, trading recommendations, or future performance.

## Current Public Posture

| Surface | Status | Public evidence |
| --- | --- | --- |
| Focused unit path | Active | GitHub Actions runs the Kraken maker shadow test suite. |
| Strategy evidence | Documented | [`docs/evidence/edge_registry.md`](./docs/evidence/edge_registry.md) preserves modeled results, limitations, and contrary evidence. |
| Experiment protocol | Documented | [`docs/experiment-protocol.md`](./docs/experiment-protocol.md) describes the graduation ladder. |
| Runtime boundaries | Documented | [`PUBLIC_REPO_BOUNDARIES.md`](./PUBLIC_REPO_BOUNDARIES.md) explains what is intentionally excluded from GitHub. |
| Historical variants | Excluded | Older broker bot variants are intentionally not part of the public recruiter snapshot. |

## Operating Model

1. Define a strategy hypothesis.
2. Test or shadow it under explicit gates.
3. Preserve both useful and negative evidence.
4. Promote only when the documented protocol is satisfied.
5. Keep generated runtime state, credentials, broker sessions, and local logs outside the public repository.

## First-Read Files

| File | Why it matters |
| --- | --- |
| [`README.md`](./README.md) | Recruiter-facing overview and reading path. |
| [`docs/evidence/edge_registry.md`](./docs/evidence/edge_registry.md) | Proof-board snapshot with limitations. |
| [`scripts/test_live_kraken_spot_frontier_maker_machinegun_shadow.py`](./scripts/test_live_kraken_spot_frontier_maker_machinegun_shadow.py) | Highest-signal test surface in the current public repo. |
| [`scripts/live_kraken_spot_frontier_maker_machinegun_shadow.py`](./scripts/live_kraken_spot_frontier_maker_machinegun_shadow.py) | Runner behavior covered by the focused test suite. |
| [`scripts/README.md`](./scripts/README.md) | Curated guide for navigating the larger script inventory. |
| [`PUBLIC_REPO_BOUNDARIES.md`](./PUBLIC_REPO_BOUNDARIES.md) | Scope, omissions, and privacy boundaries. |

## Review Notes

- Public evidence is a research filter, not a profit claim.
- Backtests and shadow tests can fail under live market conditions.
- Archived scripts may show historical exploration patterns and are not the current source of truth.
- The current public proof path is intentionally narrow so visitors can verify at least one concrete behavior surface quickly.

## Next Public Polish

1. Keep expanding focused CI around one runner at a time.
2. Move additional root helpers into `scripts/` as their role becomes clear.
3. Add more evidence pages only when their assumptions and limitations can be stated cleanly.
