# Personal data: classification, pseudonymisation and what it buys

One column in this warehouse identifies a person: `customer_id`, the pseudonym a
UK gift wholesaler's till assigned to a shopper between December 2009 and
December 2011. No name, no address, nothing to contact anyone with. It's still
personal data (pseudonymised data explicitly is, under GDPR Recital 26), and
until 2026-08-19 nothing in this project said so, nothing masked it, and every
published release shipped it in the clear from five different schemas.

This page is what was done about that, written to be read in the order the work
went: classify, measure, then decide. The measuring is the part that changed the
design, because it's what shows the obvious answer, hashing the id, to be the
smallest part of the problem.

## The vocabulary

Three labels, declared as `meta: {pii: …}` on the column, in the same ymls that
carry the descriptions and the contracts.

| Label | Means | What is done about it |
|-------|-------|-----------------------|
| `direct_identifier` | Singles out a person on its own. | Rewritten to a salted pseudonym at the publication boundary. |
| `quasi_identifier` | Singles out a person in combination with others. | **Published unchanged**, knowingly, with the combination measured below. |
| `non_personal` | Shares a name with a classified column and is deliberately not one. | Nothing, but it's stated rather than left blank. |

The third label exists because of one pair. `dim_retail_customer.net_revenue_gbp`
is revenue per *customer* and singles out 97.4% of them on its own;
`dim_retail_product.net_revenue_gbp` is revenue per *product* and identifies
nobody. Same name, opposite answer, and the only way to tell them apart is for
someone to have said so. `tests/test_privacy.py` fails if a retail column shares
a name with a classified one and carries no label of its own. That's the rule
that keeps the classification from rotting, without labelling ninety columns
nobody would ever read.

Classification starts at the **source**, not at staging. `raw` ships inside the
published DuckDB file, so a policy that begins one layer down has already let the
clear value into the artifact.

## What the labels are worth: the measurement

The interesting question isn't "is the identifier masked" but "can a person be
picked out of the published rows". Measured over the built warehouse, 5,881
identified customers, `just disclosure-risk` to reproduce:

| Columns available | Customers alone in their combination |
|---|---|
| `country` | 0.2% (10) |
| `country, cohort_month` | 2.9% (170) |
| `country, first_order_date` | 8.3% (490) |
| **`country, first_order_date, first_order_gbp`** | **99.6%** |
| `first_order_gbp, net_revenue_gbp, n_orders` (a customer extract with the id removed) | **98.6%** |
| `net_revenue_gbp` alone | 97.4% |

**The share is quoted and the count is not, and that isn't a rounding
preference.** The three rows above that involve a money column aren't
reproducible between builds: two consecutive rebuilds of `dim_retail_customer`
from byte-identical sources gave 5,781 and 5,785 distinct values of
`net_revenue_gbp`. It's `sum()` over doubles. Floating-point addition isn't
associative and DuckDB's parallel aggregation doesn't fix an order, so the last
bits of a few hundred customers' revenue differ per build, and exact equality is
what a uniqueness count is made of. The share is stable to a tenth of a point;
the count moves by single digits. Anything quoting the count as a fact is
quoting one build.

(The lake is unaffected, and the boundary is worth knowing precisely: it
archives `fct_retail_order_line`, whose money columns are per-row arithmetic
rather than aggregates, so its "byte-identical run to run" property still holds.
It's aggregation over floats that's unstable, not floats.)

**A near-continuous money column at person grain is an identifier whatever it's
called.** Deleting `customer_id` from an extract moves the number from 100% to
98.6%. That's the finding, and everything below is a consequence of it: the
pseudonymisation is worth doing and isn't a privacy story on its own, and any
claim that a customer-grain file has been "anonymised" by dropping a key is
false in a way that's cheap to check and nobody checks.

## Pseudonymisation, and the salt

`substr(sha256(customer_id || salt), 1, 16)`. Two details carry all the weight.

**The salt is required, never defaulted.** The customer ids run 12346–18287.
Building the complete unsalted rainbow table over that range, every digest for
every possible id, takes **5 ms**. An unsalted digest isn't a pseudonym, it's
the original wearing a hat, so a missing salt raises instead of falling back:

```
PII_SALT is not set. The export rewrites classified identifiers and an unsalted
digest of a five-digit id is reversed in milliseconds, so there is no safe
default.
```

**`||`, never `concat()`.** DuckDB's `concat` *ignores* NULL arguments, so
`concat(customer_id, $salt)` on a row with no customer hashes the bare salt, and
all 243,007 anonymous order lines land on one identical pseudonym that's
indistinguishable from a real customer. The warehouse would have gained a
fabricated shopper with a quarter of the business's transactions and no error
anywhere. `||` propagates the NULL, which is the truth. `tests/test_privacy.py`
pins it.

The release salt is a repository secret and is **stable across releases**. A
fresh salt each month would repseudonymise all 5,881 customers for no change in
the data, so every retail file would differ in every release and a consumer
diffing two of them could never tell a restatement from a re-salting. Stable
means a consumer can follow a customer between releases, which is what a
surrogate key is for, and still can't recover who they are. `just export-data`
generates a throwaway salt locally, because a laptop's copy isn't a release and
shouldn't look like one.

## Why the boundary and not the model

The obvious place to mask a column is the staging model that reads it, so that
nothing downstream ever sees the clear value. That doesn't work here, for two
reasons that only appear once you look at the published artifact rather than at
the warehouse:

* **`raw` ships too.** The release is a `COPY FROM DATABASE` of everything, so a
  mask applied in staging leaves the original one schema away in the same file.
* **The staging models are views.** A view in the published copy recomputes from
  `raw` when a consumer queries it. Mask the raw column *as well* and the view
  hashes an already-hashed value, so the shipped views and the shipped marts
  disagree about who each customer is, with matching row counts and no error.

Applying the policy once, to the finished copy, avoids both: base tables are
rewritten, views recompute from the rewritten tables, and the two agree because
the value was hashed exactly once. `tests/test_privacy.py` builds that exact
shape (landing table, view, mart) and asserts the join still lands.

## The columns nobody would have declared

`customer_id` appears in **51 relations** in this warehouse. Six are declared.
The policy therefore expands the declared set **by column name** across every
schema in the copy before rewriting, and the difference isn't academic:

* **`raw_staging.retail_invoice_lines`**, dlt's merge scratch. A full copy of
  the landing table that no yml describes and nothing downstream reads. Every
  release published before this work shipped it with 1,067,371 rows, 824,364 of
  them carrying a clear id.
* **44 `dbt_test__audit` tables.** `store_failures` is on project-wide, so every
  failing row of every retail test is written to a table the published database
  then carries. They're empty today because the tests pass, meaning this leak
  opens on the day something goes wrong and closes again before anyone looks.

Declaring the column is still the contract; the sweep is what makes forgetting it
fail closed rather than ship. The export **verifies** after it rewrites. Every
value of every covered column must match `^[0-9a-f]{16}$` or be NULL, which is
decisive rather than heuristic, because a five-digit customer number can't match
that pattern.

## Access control: what DuckDB cannot do

There's no row-level security here, and it's worth being precise about why
rather than listing it as a gap. DuckDB 1.5.5 has no access-control surface at
all: `create role`, `grant`, `create user` and `create policy` are each a parser
error, not an unsupported feature. There's no user to attach a policy to because
there's no concept of a user. Whoever holds the file holds everything in it.

So the enforcement point can't be the database, and pretending otherwise with a
"restricted" schema that nothing prevents anyone from reading would be theatre.
It's the **boundary** instead: the export, which is the only moment this data
crosses from a machine that has it to a machine that doesn't. That's a real
architecture and not a workaround. It's where a warehouse with roles would put
its masking policy too, applied to the extract rather than to the query.

## The decisions

Every finding here ends in one.

* **The identifier is pseudonymised in the release.** Salted, stable, verified
  after the fact, covering all 50 columns that carry it across five schemas.
* **The quasi-identifiers ship unchanged**, and the 98.6% above says what that
  means. They're publishable because the source is already public: UCI
  redistributes the whole transaction log under CC BY 4.0, ids included, so
  nothing in the release discloses anything the internet doesn't already have.
  **The day that stops being true, this decision has to be remade**, and the
  honest form of it then is aggregation, not more masking.
* **The Evidence site stopped shipping what no chart drew.**
  `sources/warehouse/retail_rfm.sql` was `select *`, 19 columns including the
  id, the country and three dates, downloaded by every visitor to render four.
  It's four columns now. `retail_customers.sql` had already picked its columns
  and had kept `customer_id` anyway, which no chart on the page ever read.
  `retail_returns.sql` was `select *` too, 23 columns to render three, carrying
  17,934 clear identifiers over 2,445 customers. It survived the first pass of
  this work precisely *because* it named no columns: grepping the source queries
  for `customer_id` can't find a query that mentions no column at all, which is
  the argument for a column list being the default rather than the exception.
  Pruning is worth less than it looks (99.8% of customers are still unique on the
  four columns that remain in the RFM extract) and is still worth doing: nothing
  can be joined to a file with no join keys in it.
* **The scatter stays at customer grain.** A chart of first-order value against
  lifetime value is one mark per person; there's no aggregate that draws it. The
  alternative was to delete the chart, and the finding it carries, that
  first-order value predicts lifetime value on ranks and not on Pearson *r*, is
  worth more than the marginal disclosure of a money pair already in a public
  file.

## What is not done

* **No row or column access control in the warehouse itself**, per the DuckDB
  section above. A consumer of the release gets everything in it.
* **No k-anonymity guarantee, and no attempt at one.** The numbers above are a
  measurement, not a threshold that anything enforces. Enforcing k≥5 on a
  customer-grain table would mean generalising money into bands, which would
  delete the analysis the table exists for.
* **No retention or erasure story.** The extract closed in 2011 and its subjects
  are unreachable by construction. There's nothing to action a request against,
  which is a property of this dataset rather than a design.
* **Nothing outside retail is classified.** Every other source here is published
  national statistics: country-year aggregates with no person in them. A label of
  `non_personal` on all 300 of those columns would be paperwork, and the test
  only requires one where a name collides with something that is.
