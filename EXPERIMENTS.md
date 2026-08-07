# EXPERIMENTS

Append-only. One dated line per experiment: hypothesis → result → decision.

---

## Phase 1 — API verification

**2026-08-06 · Does the FDIC API in the brief work as documented?**
Hypothesis: `banks.data.fdic.gov/api/financials` serves the quarterly panel.
Result: 301-redirects to `api.fdic.gov/banks/financials`. New host returns 200, no key,
4,640 banks for 2024Q1. Latest published quarter is 2026Q1.
Decision: target `api.fdic.gov/banks` directly; note the stale host in PLAN.md.

**2026-08-06 · Are the brief's assumed field names real?**
Hypothesis: brokered deposits, time deposits, and AOCI are absent from `/financials`.
Result: **False.** The default response is a 161-field subset of a **2,378-field** dictionary.
`BRO`, `NTRTIME`, `SCAA`, `SCAF`, `DEPUNINS`, `DEPNI`, `COREDEP` all exist when named
explicitly. Unknown fields are **silently dropped with HTTP 200** — no error.
Decision: (a) candidate #6 (unrealized securities loss) is computable as `SCAF − SCAA` and is
promoted from PARKED to a first-class candidate; (b) `data.py` validates every field against
the dictionary before the request and asserts presence after, because a typo'd field name
would otherwise become a silently missing column and a silently broken signal.

**2026-08-06 · Is `DEPUNINS` coverage a post-SVB artifact?**
Hypothesis: uninsured-deposit reporting only became universal after March 2023, which would
make any signal built on it a regime artifact.
Result: **No.** 100% populated in every sampled quarter from 2015Q1. All 15 key fields are
≥99% populated across the full window.
Decision: `uninsured_dep_share` is admissible as a candidate over the whole panel.

---

## Phase 2 — labels

**2026-08-06 · Baseline label build.**
Result: 236,016 raw bank-quarters → 198,280 retained; base rate 5.75% (≤−5%), 1.18% (≤−10%).
Reconciliation checks passed: rows with no T+1 (6,590) = unique bank count exactly (each bank
has one terminal row); 6,590 − 2,238 mid-panel exits = 4,352 = the 2026Q1 bank count.
Decision: proceed to tail inspection before trusting the base rate.

**2026-08-06 · Are the most extreme "drawdowns" real?**
Hypothesis: the −5% rule cleanly captures organic outflow.
Result: **No.** The 8 most extreme events all had `dep_next == 0.0` — banks (Wells Fargo Bank
NW NA, Allied Irish, Marlin Business Bank) that *filed* a T+1 report while surrendering the
charter. My exclusion only caught banks whose T+1 row was **absent**, not those reporting zero.
45 of 11,393 positives (0.39%) sat below the materiality floor at T+1.
Decision: add `terminal_winddown` (T+1 deposits below the same $10M floor required at T).
Small in count but it occupied the extreme tail, which dominates precision@k — a model would
have learned to predict charter wind-downs and scored well doing it.

**2026-08-06 · Can `ACTEVT` identify structure events?**
Hypothesis: FDIC's own activity-event code flags mergers/closings on the affected rows.
Result: **No.** Populated on 23,552 rows overall but only 2 of the 68 extreme declines.
Decision: rejected as a filter.

**2026-08-06 · Can an affiliate's offsetting same-quarter gain identify a book transfer?**
Hypothesis: if bank *i* loses $X and a same-parent sibling gains ≈$X in the same quarter, it's
an intra-group transfer rather than an outflow.
Result: **Confounded.** Wilmington Trust NA's −$11.1B transfer into M&T scored an offset ratio
of −0.45, because M&T was *also* shedding deposits organically in 2022Q1. Fires correctly for
Wells Fargo NW (4.45) but not reliably.
Decision: rejected. A blanket "exclude pre-merger quarters" rule was also rejected — it would
have removed Silvergate 2022Q4, the single most informative genuine run in the panel.

**2026-08-06 · `RSSDHCR` sentinel bug (found while validating the above).**
Hypothesis: comparing `RSSDHCR` identifies same-holding-company affiliates.
Result: **Bug.** `RSSDHCR` is a *string* column where "no holding company" is an **empty
string** (6,756 institutions), not null. Since `"" == ""` is True, every independent HC-less
bank merging into another independent bank was flagged as an intra-group consolidation and
wrongly excluded — 131 rows over-excluded.
Decision: added `_clean_id()` normalising `""`/`"nan"`/`"0"` → NA, plus two regression tests.
This is why exclusion rules get the same skepticism as features: an over-broad filter quietly
deletes real events.

**2026-08-06 · Is drawdown rate uniform across charter classes?**
Hypothesis: charter class is incidental.
Result: **No.** `BKCLASS=NC` (non-insured US branches of foreign banks) runs a **33.7%**
quarterly drawdown rate and `OI` (insured foreign branches) **22.9%**, against a ~5.7% panel
base rate — 0.3% of rows but 1.5% of all positives, from just 17 banks. Bank of China alone
appears 4× in the extreme tail.
Decision: exclude `NC`/`OI` as a class. These are wholesale funding vehicles, not deposit
franchises; left in, a model learns "is this a foreign branch" and scores well while learning
nothing about deposit behaviour. Preferred over hand-listing 6 individual rows.

**2026-08-06 · Residual extreme declines.**
Result: 22 rows remain at ≤−50% after the systematic rules. 6 are verifiable structure events
(intra-HC consolidations at Wilmington Trust / Wells Fargo NW / Chase Bank USA; the 2022 ANZ
Guam divestiture; John Deere Financial's shift to parent funding). Silvergate 2022Q3+Q4 and
Hatch Bank 2022Q3 are genuine concentrated-funding runs and are kept.
Decision: the 6 go in `data/manual/label_exclusions.csv` with a written reason each; the rest
are **kept**, including community-bank cases I could not verify. Excluding on suspicion alone
would be tuning the dataset; keeping them adds label noise but no optimism bias.

**2026-08-06 · Final labels.** 196,150 rows (83.1% of raw), 6,025 banks, 2016Q1–2025Q4.
Base rate **5.62%** (11,027 events), severe **1.11%** (2,169). Effective start is 2016Q1
because trailing features need 5 quarters of history.

**2026-08-06 · Does labelling on `DEPDOM` instead of `DEP` change the event set?** (open Q1)
Result: **No.** 99.977% agreement; 45 disagreements out of 196,150 rows (11,027 vs 11,020
positives).
Decision: keep `DEP`; the foreign-deposit concern is immaterial once `NC`/`OI` are excluded.

**2026-08-06 · Is a flat −5% threshold size-comparable?** (open Q3)
Result: Partly. <$300M banks 6.83%, $300M–1B 4.23%, $1–10B 4.17%, $10–100B 4.41%, >$100B
5.43%. Not wildly size-dependent, but small banks are noisiest and dominate the panel
(104,675 of 196,150 rows; only 42 banks above $100B).
Decision: keep −5% for comparability; report precision@k **split by size bucket**, since an
unsplit top-k list will be almost entirely small banks.

**2026-08-06 · Seasonality of the event.** (open Q2)
Result: Real but moderate, and strongest in **Q2** (mean 6.79% vs 5.2–5.4% elsewhere) —
consistent with April tax payments draining balances. Regime effect dwarfs it: 2022Q4 12.86%,
2023Q2 12.30% vs 2020Q2 1.16% (COVID stimulus inflows).
Decision: carry quarter-of-year as a control and test whether it changes graduation verdicts.

---

## Phase 3+ — pending

Next: feature/registry construction with the automated as-of audit, then baselines. No model
has been fitted yet; base rates above are the pre-model checkpoint.
