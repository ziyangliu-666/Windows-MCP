# Contributing

This fork is not maintained like a generic feature repo.
It is maintained as a reliability-hardening fork of Windows-MCP.

The best contributions are the ones that make later autonomous desktop runs:

- more reliable
- easier to verify
- easier to debug

## Before You Change Anything

Start by identifying which kind of change you are making:

- bug fix for a reproduced failure
- benchmark or verification improvement
- routing improvement toward a more reliable non-UI path
- diagnostics that unblock validation
- documentation or maintenance work

If the change is reliability-related, try to tie it to an observed failure or benchmark gap.

## Development Setup

```bash
uv sync
uv run pytest -q
```

Run the server locally:

```bash
uv run windows-mcp
```

Useful research helpers:

```bash
uv run python research/experiments/run_local_suite.py
uv run python research/experiments/notepad_type_probe.py
uv run python research/experiments/internal_transport_probe.py
```

## Expected Workflow

For code that affects reliability, prefer this loop:

1. Reproduce the issue or run the target benchmark.
2. Capture the exact failure.
3. Patch the narrowest relevant cause.
4. Add or update automated tests if possible.
5. Retest locally.
6. Retest live when the real MCP path matters.
7. Update the minimum research docs needed for handoff.

## Commit History Rules

The current repo history is readable, but it is too easy for autonomous runs to produce noisy checkpoint commits.
Going forward, keep the history tighter.

Use commit subjects in imperative mood:

- `Fix browser DOM scrape metadata lookup`
- `Add protocol launch support for Settings`
- `Benchmark stale-state recovery loop`

Avoid commit subjects like:

- `update`
- `misc changes`
- `more notes`
- repeated `Log ...` commits that only reshuffle status without changing the code or the benchmark state

### Preferred Commit Shape

For reliability work, one commit or one small related series should contain:

- the patch
- the regression test
- the benchmark or verification update

Do not mix unrelated fixes just because they happened in the same session.

### Branching

Prefer short-lived topic branches over piling checkpoint commits directly onto `main`.

Suggested naming:

- `fix/focus-switch-postcondition`
- `fix/worker-transport-timeout`
- `bench/stale-state-recovery`
- `docs/research-workflow`

If a branch accumulates many small AI-generated checkpoint commits, squash before merge unless the intermediate steps have real forensic value.

## Testing Expectations

At minimum:

```bash
uv run pytest -q
```

For targeted changes, also run the most relevant focused tests.

Examples:

```bash
uv run pytest tests/test_app_service.py -q
uv run pytest tests/test_dev_hot.py -q
uv run pytest tests/test_scrape_handler.py -q
```

If a change affects live automation behavior, record the live verification outcome in `research/results/`.

## Documentation Expectations

Keep docs aligned with the fork's actual purpose.

When you change behavior:

- update `README.md` if the public explanation changed
- update `research/patches.md` if the system changed
- update `research/test_matrix.md` if benchmark status changed
- update `research/next_session.md` with the next narrow action

Do not duplicate the same information across every research file.

## Pull Request Checklist

- the change is scoped to one main problem
- tests relevant to the change were run
- live verification was recorded if needed
- commit history is readable
- research docs were updated minimally and deliberately
- no broad speculative redesign was mixed into a narrow bug fix

## Safety

This repository controls a real Windows desktop environment.

Use a VM, disposable user profile, or dedicated test machine when possible.
Prefer filesystem, process, shell, registry, DOM, or protocol routes over blind UI automation when they can satisfy the task more reliably.
