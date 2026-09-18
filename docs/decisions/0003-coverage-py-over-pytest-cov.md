# 0003. Coverage runs as `coverage run -m pytest`, not through pytest-cov

Status: accepted 2026-08-26 (#24)

## Context

`just coverage` measures line and branch coverage of the mocked tier. The usual
way to do that is the `pytest-cov` plugin and a `--cov` flag.

## Decision

pytest runs *under* coverage.py (`coverage run -m pytest`), configured in
`pyproject.toml`, with no plugin.

## Rejected

- **`pytest-cov`**, tried first and dropped the same day. It measured
  identically, with the same total and the same runtime to within 0.02 s, for one
  more package. The plugin was buying a `--cov` flag; the configuration it reads
  is coverage.py's either way.

## Consequences

- There is no flag to leave switched on by accident, so coverage can never slow
  `just test` or CI by default.
- Coverage gates nothing: no `fail_under`, and nothing in CI runs it.
