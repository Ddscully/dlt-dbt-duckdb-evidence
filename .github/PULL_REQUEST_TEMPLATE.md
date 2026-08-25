<!--
Thanks for the PR. Every PR here is squash-merged, so this description becomes
the commit message on `main` — it is worth writing as one.
-->

## What this changes, and why

<!-- The problem, not just the diff. If it fixes an issue, link it. -->

## Checks

- [ ] `just test` passes
- [ ] `just test-pipeline` passes (this is what CI runs)
- [ ] `just lint` passes, and `pre-commit` is installed
- [ ] Any count I wrote in prose is one the dbt manifest actually produces
      (`tests/test_documented_counts.py` checks this)

## If this touches a model or a source

- [ ] I ran the pipeline and looked at the result, rather than assuming
- [ ] New or changed models have tests, and I checked that those tests fail when
      the model is wrong — a test that cannot go red is not a test
- [ ] Grain, contracts and documentation are updated alongside the SQL

<!--
On that second box: this repo's testing convention is mutation. Break the model
in a plausible way against a *copy* of the warehouse, run its tests, and record
what moves. Across five models mutated this way, 24 mutations were run and the
data tests caught 3 — so "nothing went red" is a finding, not an all-clear.
-->
