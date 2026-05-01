# Public Repo Boundaries

This repository is the public proof layer for a larger local trading-research workspace. It is intentionally curated to show the engineering system, validation discipline, representative tested code, and safe evidence artifacts.

## Included

- Research and strategy code that can be reviewed without broker access.
- Focused unit tests and GitHub Actions CI.
- Architecture, experiment protocol, and operator documentation.
- Evidence snapshots such as proof boards, performance reviews, and validation summaries.
- Historical bot variants that help explain the project evolution.

## Excluded

- Credentials, API keys, tokens, account IDs, and private broker configuration.
- Live or shadow runtime state, generated learning payloads, and account-specific snapshots.
- Local logs, debug dumps, temporary probes, and generated reports.
- Local agent settings, chat/task stores, and machine-specific orchestration files.
- Any material that would expose private operating details rather than public engineering evidence.

## Why This Boundary Exists

The goal is to make the repository reviewable without publishing unsafe or noisy operating artifacts. A public portfolio repo should let a visitor inspect architecture, tests, validation methods, and representative implementation while keeping credentials, live-state payloads, and machine-local workflows private.

This boundary is part of the engineering standard: the system is designed to separate proof surfaces from operational state.
