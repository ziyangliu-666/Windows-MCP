# AGENTS.md

This repository is for autonomous reliability research, not generic feature work.

## Mission

Improve the reliability, repeatability, and debuggability of Windows desktop automation on a real machine.

## Required Loop

1. Reproduce one real failure or run one benchmark.
2. Record the observed behavior.
3. Patch the narrowest cause.
4. Add or update tests when possible.
5. Retest locally.
6. Retest live if the real MCP path changed.
7. Update only the minimum research files needed for handoff.

Do not make strong success claims without a retest.

## Priorities

1. reproduced bugs on the real machine
2. missing postcondition verification
3. repeatable benchmarks for known flaky workflows
4. stronger non-UI or semantic routing
5. diagnostics that unblock validation

## Verification Rules

- Do not trust tool success strings alone.
- Prefer `Snapshot`, filesystem, clipboard, process, shell, or DOM checks.
- Keep blind UI action chains to `1`.
- If the same workflow fails three times without a system change, stop and classify it.
- Prefer stronger routes when they exist:
  - DOM over browser chrome clicking
  - protocol URIs over click-navigation
  - filesystem/process/shell checks over title guessing

## Research Files

- `research/failure_taxonomy.md`: confirmed failure classes and examples
- `research/test_matrix.md`: benchmark definitions and latest status
- `research/patches.md`: implemented and planned changes
- `research/next_session.md`: short handoff only
- `research/results/*.md`: benchmark or repro evidence
- `research/notes.md`: short summary, not a second source of truth

Do not repeat the same fact across all of them.

## Commit Hygiene

- Prefer a few coherent commits over checkpoint spam.
- Bundle patch, test, and verification when they belong together.
- Avoid repeated `Log ...` commits with no new code or benchmark value.
