# Next Session

## Current Status

- The main reliability fixes from this session are already patched and retested.
- Hot mode is usable again for real live validation.
- The remaining open work is no longer basic transport breakage. It is benchmark depth and repeatability.

## Strongest Current Conclusions

- Persistent stdio is the right hot-worker transport.
- `App.switch` is much better than before, but recovery still needs broader cross-app sampling.
- For browser tasks, DOM is stronger than desktop clicking on normal pages, but interactive landing pages still need a separate click-trigger route.
- For Settings tasks, protocol launch is stronger than in-app click navigation.
- Stale-state handling is now good enough to use as a normal supervisor primitive.

## Next 5 Actions

1. Turn focus recovery into a repeatable benchmark across at least two foreground app classes, not just one-off passes.
2. Run the hard `Background app launch then attach` benchmark from `research/test_matrix.md`.
3. Run the hard `Retry after UI change` benchmark on a real re-rendering UI path.
4. If `App.switch` still misses in those runs, add and verify a supervisor fallback that activates a visible taskbar or window target from the latest snapshot.
5. Use the Genshin-style landing page as the browser benchmark for "click-triggered entry, not scroll/document flow", then verify download state via filesystem rather than browser text.

## Do Not Drift Into

- rewriting large summaries
- broad architecture comparison
- more shell diagnostic work unless it blocks a real benchmark
- repeated one-off demos without measured postconditions
