# Research Folder Guide

This folder stores the reliability research trail for this fork.

The goal is not to keep every thought forever.
The goal is to preserve the minimum evidence needed to continue improving the automation stack.

## Source Of Truth

Use these files for these jobs:

- `failure_taxonomy.md`
  - canonical list of failure buckets
  - confirmed failure entries with evidence and suggested fixes
- `test_matrix.md`
  - benchmark definitions
  - expected postconditions
  - latest benchmark state
- `patches.md`
  - concrete planned and implemented changes
  - do not turn this into a narrative diary
- `next_session.md`
  - short handoff for the next run
  - should contain the next narrow actions, not a full retrospective
- `results/*.md`
  - one timestamped file per benchmark or repro run
  - should include goal, preconditions, steps, result, and interpretation

## What `notes.md` Is For

`notes.md` is a rolling summary for humans who want the current picture quickly.

It should:

- summarize the strongest findings
- point to benchmark and patch evidence
- stay shorter than the full set of result logs

It should not:

- duplicate every benchmark result
- become the second source of truth for patch state
- restate the whole session after every small step

## Minimal Update Policy

After a code change or benchmark run, update only what is necessary:

- add a `results/*.md` file if a real benchmark or repro was run
- update `patches.md` if the system actually changed
- update `test_matrix.md` if benchmark status changed
- update `next_session.md` for the next operator
- touch `notes.md` only if the high-level picture changed

In many cases, `notes.md` should not change.

## Session Checklist

For a normal reliability session:

1. Pick one benchmark or reproduced failure.
2. Run it and capture evidence.
3. Classify the result.
4. Patch only if the failure is understood enough.
5. Retest.
6. Record the outcome in the smallest correct set of files.

## Smells

The session is drifting if any of these start happening:

- more time is spent rewriting notes than running benchmarks
- the same fact appears in four files
- commits say `Log ...` repeatedly without a new patch or verification gain
- architecture discussion starts before the known reproduced bug is fixed
- "success" is claimed from tool return strings without state verification

## Keeping It Sustainable

Prefer fewer, higher-signal artifacts over exhaustive narration.

If the folder starts feeling noisy:

- consolidate repeated status into `notes.md`
- keep `next_session.md` short and actionable
- leave raw benchmark detail in `results/*.md`
- stop updating files that are not acting as the source of truth
