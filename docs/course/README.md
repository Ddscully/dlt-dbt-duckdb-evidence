# A data-engineering course, taught off a warehouse that works

Ten modules that use this repo as the worked example. It is aimed at people who
can already write SQL and have edited a dbt model, and who want the part nobody
teaches: **the failures that stay green.**

## Why this repo, and why these failures

A tutorial pipeline is built once and never restated, never re-run against a
source that moved, never published to someone who cannot be paged. So its
lessons are about syntax. Everything expensive in this trade is somewhere else:

| The bug | What it looked like |
|---|---|
| `concat(customer_id, salt)` instead of `\|\|` | DuckDB's `concat` ignores NULLs, so 243,007 anonymous rows hashed the bare salt and landed on **one** pseudonym — indistinguishable from a real customer |
| `.arrow()` handed straight to dlt | stored exactly **1,000,000** of 1,067,371 rows; the round number was the only clue |
| `cast(… / 3 + 1 as integer)` for a fiscal quarter | March under an April year start is 4.67, which casts to **quarter 5** |
| a per-column `coalesce` for a fallback row | paired one country's tonnage with another row's mark-up and produced a 100% implied tax rate that exists nowhere in the regulation |
| `where year = (select max(year) …)` | a cross-section of grid carbon intensity that drops **115 of 205 countries** |

None of those raised. Every one produced a number a reviewer would accept. That
is the subject of the course, and this repo is the material because it has
**440 dbt tests, 17 enforced contracts and an offline fixture harness** you can
break in a sandbox and rebuild in under a minute.

## How a module works

Each one is: what the layer does here → the decision inside it that has a wrong
answer → exercises. Three kinds, and they are marked:

- 🔧 **Break-and-fix.** You are given a symptom: a number that is wrong and a
  build that is green. You seed the bug into the sandbox yourself, find it from
  the evidence, and fix it. Every drill ends with a **verification query**,
  because "it looks right now" is the failure mode the course exists to break.
- 🔍 **Investigate.** A question the warehouse answers and intuition does not
  ("did European electricity rise 35% or 13.5%?"). Run against the **real**
  warehouse, not the sandbox: coverage is usually the point, and the sandbox
  has 17 countries.
- 💬 **Design defence.** No code. "Why is the snapshot the only table here that
  a rebuild cannot reproduce?" Answer it out loud or in writing before opening
  the reveal; these are the questions an interview and a design review both ask.

Answers sit in collapsed `<details>` blocks. Opening one before you have written
something down is the only way to waste this material.

## Setup

**[`00-setup.md`](./00-setup.md) first**: it builds the sandbox and explains the
two data modes the exercises switch between. Roughly:

```bash
just setup           # once per clone
just course-sandbox  # ~50s, offline, into data/course/ (gitignored)
just course-query 'select count(*) from marts.dim_country_year'
```

## The modules

| # | Module | The decision it is about |
|---|---|---|
| 00 | [Setup and the sandbox](./00-setup.md) | why the exercises run against two different warehouses |
| 01 | [Grain is the contract](./01-grain.md) | what one row means, and what a fact hangs off |
| 02 | [Loading twice](./02-loading-twice.md) | replace vs merge, watermarks, lookback windows, what makes a re-run safe |
| 03 | [Tests that fail on bugs, not on reality](./03-tests.md) | calibrating a bound against a distribution instead of a hope |
| 04 | [Denominators, units and coverage](./04-denominators.md) | current vs constant, per-column latest year, spot vs average |
| 05 | State vs build artifacts | which tables a rebuild cannot reproduce, and what that costs |
| 06 | Contracts, access and versions | changing a column under a consumer you cannot page |
| 07 | The orchestration graph | asset keys as the join between layers, and how a graph splits in silence |
| 08 | Personal data at the publication boundary | classification, pseudonymisation, and why deleting the id is not anonymisation |
| 09 | Reproducibility | fixtures, cache keys, float non-determinism, offline CI |
| 10 | Publishing, and what breaks at 1000× | releases, attribution, and the honest limits of this design |

Modules 05–10 are outlined but not yet written; 00 through 04 are complete and
set the format.

## The rest of the documentation

The course teaches; [`docs/`](../) explains and [`CLAUDE.md`](../../CLAUDE.md)
records what each lesson cost to learn. When a module wants the full reference
it links there rather than restating it: a fact in two places drifts in one.
