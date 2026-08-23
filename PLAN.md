# flightrisk - PLAN

**Goal.** One quarter ahead, rank US banks by probability of a significant deposit
drawdown, using only information observable as of the prior quarter. Public analog for
"which accounts will pull treasury next quarter."

**Shipped as** an installable package + CLI + signal registry, not a notebook. The
registry is the single source of truth; scorecards and the README results table are
generated from it.

Status of this document: written **after** verifying every API endpoint, field name, and
coverage claim below against the live APIs on 2026-08-06. Nothing here is assumed.

---

## 1. Data source - VERIFIED, with corrections to the original brief

### 1.1 The host in the brief is stale

`https://banks.data.fdic.gov/api/` **301-redirects** to `https://api.fdic.gov/banks/`.
All code will target the new host directly. No API key needed (confirmed HTTP 200).

### 1.2 Endpoints used

| Endpoint | Use | Verified |
| --- | --- | --- |
| `GET api.fdic.gov/banks/financials` | quarterly panel, one row per `CERT`×`REPDTE` | 4,640 rows for `REPDTE:20240331` |
| `GET api.fdic.gov/banks/institutions` | metadata, de novo dates, merger successor | 4,254 active; 23,582 inactive+active |
| `GET api.fdic.gov/banks/history` | structure-change events (merger/failure) | 583,724 records |

Data dictionary: `https://api.fdic.gov/banks/docs/risview_properties.yaml` (616 KB,
**2,378 fields**). Cached to `data/raw/` and parsed for signal provenance.

### 1.3 Three API gotchas that change the code design

1. **Unknown field names are silently dropped, not errored.** Requesting
   `fields=CERT,TIMEDEP` returns `{"CERT":628,"ID":"..."}` with HTTP 200 and no warning.
   A typo'd field becomes a silently-absent column → a silently-broken signal.
   *Mitigation:* `pull` validates every requested field against the 2,378-field dictionary
   before the request, and asserts every requested column is present in the response.
   This is a hard failure, not a warning.
2. **The default response is a 161-field subset of 2,378.** Fields the brief assumed
   missing (`BRO`, `NTRTIME`, `SCAA`) do exist - they just aren't returned by default and
   must be named explicitly.
3. **Both list endpoints cap at `limit=10000`.** `financials` fits in one request per
   quarter (max ~6.5k banks). `institutions` (23,582 total) requires `offset` pagination.

### 1.4 Coverage check - all key fields ≥99% populated, 2015Q1→2026Q1

Measured non-null share across all banks in 8 sampled quarters:

```
20150331 n=6496  DEP:100% ASSET:100% EQ:99% BRO:99% COREDEP:99% DEPUNINS:100%
                 DEPNI:99% NTRTIME:99% SCAF:100% SCAA:100% NIMY:100% LNLSDEPR:100%
20230331 n=4740  ... identical coverage ...
20260331 n=4352  ... identical coverage ...
```

This matters: had `DEPUNINS` only existed post-SVB, any signal built on it would be a
regime artifact rather than a signal. It doesn't - it goes back to 2015 at 100%.

**Latest available quarter is 2026Q1** (`REPDTE=20260331`). 2026Q2 Call Reports are not
yet published as of 2026-08-06, so the live `score` demo targets 2026Q1.

Bank count falls 6,496 → 4,352 (−33%) over the window. That attrition is consolidation,
and mishandling it is the single largest correctness risk in this project (§3).

### 1.5 Exact fields pulled (all verified to exist and be populated)

**Deposits / funding:** `DEP` (total deposits - the label numerator), `DEPDOM`,
`DEPNI` (noninterest-bearing), `NTRTIME` (total time deposits), `BRO` (brokered),
`COREDEP` (core), `DEPINS` (est. insured), `DEPUNINS` (est. uninsured).
**Balance sheet:** `ASSET`, `EQ`, `LIAB`, `LNLSNET`, `SC`, `SCAF` (AFS at fair value),
`SCAA` (AFS at amortized cost).
**Precomputed ratios (used for cross-checks, not as features):** `LNLSDEPR`, `EQV`,
`NIMY`, `ROA`, `ROE`, `BROR`.
**Keys / metadata:** `CERT`, `REPDTE`, `NAME`, `BKCLASS`, `STALP`, `ASSET`, `ACTEVT`.

**Upgrade vs. the brief:** candidate #6 (unrealized securities losses) does **not** need
to be parked. `SCAF − SCAA` = unrealized AFS gain/loss, both fields at 100% coverage since
2015. This is the actual SVB mechanism, so it is a first-class candidate.

---

## 2. Event definition

For bank *i*, quarter *T*, with deposits `DEP`:

```
qoq_growth(i, T+1) = (DEP[i,T+1] - DEP[i,T]) / DEP[i,T]

label_5(i,T)  = 1 if qoq_growth(i,T+1) <= -0.05     # primary
label_10(i,T) = 1 if qoq_growth(i,T+1) <= -0.10     # severe tier
```

The label is attached to the **feature row at T**, so row (i,T) carries only
as-of-T features and the T+1 outcome. Prediction target is always the next quarter.

### Exclusions (a row is dropped, not labelled 0)

| Rule | Mechanism | Why |
| --- | --- | --- |
| Bank exits between T and T+1 | no `REPDTE=T+1` row for `CERT` | absence ≠ drawdown; label undefined |
| Bank was acquired | `institutions.NEWCERT` on the inactive record points at successor | merger exit, not organic outflow |
| Bank **absorbed** another in T+1 | `CERT` appears as some other bank's `NEWCERT` with `ENDEFYMD` in T+1 | inorganic deposit *jump* distorts growth |
| De novo / too-short history | `ESTYMD` within 8 quarters of T, or <5 quarterly obs | growth rates on a tiny base are noise |
| Degenerate base | `DEP[i,T]` < \$10M | −5% on a \$2M book is rounding |

`NEWCERT` is populated on 97% of inactive institutions (verified), and grouping by it
identifies acquirers - e.g. CERT 22559 absorbed two banks in 2023–24. `/history` with its
`FAILED_*` / `UNASSIST_*` flags is the cross-check on failures vs. voluntary mergers.

Every exclusion is **counted and reported** in `build` output and the README, so the reader
can see exactly how many rows each rule removed.

### Base rate
Reported per quarter, not just pooled - a rare-ish event whose rate moves with the rate
cycle. **These numbers are produced before any model is built** (next deliverable).

---

## 3. Candidate signals - 13 total, all strictly as-of T

Each is a registry entry: `name`, one-line `rationale`, `compute` fn, `status`, `evidence`.

| # | Signal | Fields | Economic rationale |
| --- | --- | --- | --- |
| 1 | `dep_growth_decel` | `DEP` | Δ in QoQ growth over trailing 2–4q; outflows start before they show up as a level break |
| 2 | `brokered_share_chg` | `BRO`,`DEP` | Brokered funding is hot money that leaves on price, not relationship |
| 3 | `time_dep_share_chg` | `NTRTIME`,`DEP` | Rate-sensitive funding reprices and walks at maturity |
| 4 | `loan_to_dep_trend` | `LNLSNET`,`DEP` | High/rising LTD = funding already stretched, no cushion to absorb an outflow |
| 5 | `equity_assets_chg` | `EQ`,`ASSET` | Thin//falling capital cushion invites depositor and regulator scrutiny |
| 6 | `unrealized_afs_loss` | `SCAF`,`SCAA` | (fair − amortized)/equity: paper losses that become real if deposits force a sale - the SVB mechanism |
| 7 | `asset_dep_divergence` | `ASSET`,`DEP` | Assets growing faster than deposits = funding gap filled by borrowings |
| 8 | `nim_compression` | `NIMY` | Margin squeeze signals the bank is losing the deposit-pricing fight |
| 9 | `log_assets` | `ASSET` | Size - **expected confounder**, registered explicitly so it can be tested and controlled |
| 10 | `prior_drawdown` | `DEP` | Persistence: outflows arrive in runs, not single quarters |
| 11 | `uninsured_dep_share` | `DEPUNINS`,`DEP` | Uninsured balances are the flight-risk tranche; the most-cited SVB metric |
| 12 | `noninterest_dep_share_chg` | `DEPNI`,`DEP` | Operational (noninterest-bearing) balances are the stickiest; their decline is an early tell |
| 13 | `core_dep_share_chg` | `COREDEP`,`DEP` | Direct FDIC measure of relationship vs. purchased funding |

**Deliberately dumb candidates** (3, so graduation has something to kill):
`cert_number_parity` (CERT is even), `state_alpha_rank` (alphabetical rank of `STALP`),
`digit_sum_assets` (digit sum of `ASSET`). All three are semantically null. If any
"graduates," the framework is broken - they are a live tripwire on the whole pipeline, not
filler.

---

## 4. Unstructured leg - SEC EDGAR 8-K Item 2.02

Transcripts are paywalled; EDGAR is free, complete, and timestamped. Verified working.

- **Panel.** Bank holding companies from `company_tickers.json` (10,398 companies;
  387 name-matched bank-ish candidates), filtered to SIC `6020/6022/6035/6712` via
  `data.sec.gov/submissions/CIK##########.json` (verified: returns `sic`, `sicDescription`,
  and full filing history with a per-filing `items` field).
- **Documents.** 8-K filings whose `items` contain **`2.02`** (Results of Operations)  - 
  the quarterly earnings press release, usually `ex99-1.htm`. Enumerating *all* Item 2.02
  8-Ks for a fixed panel avoids the selection bias of full-text-searching for
  "deposit outflows" (which returns 1,089 hits precisely because they mention it).
- **Crosswalk.** EDGAR company name → FDIC `NAMEHCR` (holding-company name, 83% populated)
  → `CERT`. Normalized fuzzy match, cached to `data/raw/crosswalk.csv`, **hand-verified**,
  with unmatched entries reported rather than silently dropped.
- **Extraction.** Fixed, versioned prompt (`extraction/prompts/v1.py`, hash recorded with
  every output) → per bank-quarter flags: `deposit_pressure_mentioned` (y/n),
  `funding_concern_tone` (0–4), `outflow_language` (outflow/inflow/neutral),
  `competition_for_deposits` (y/n). All raw LLM responses cached to
  `data/raw/llm/{cik}_{accession}.json`; re-runs are cache hits, so results are reproducible.
- **Validation before trust.** ~30 hand-labelled documents; report per-field agreement.
  If extraction accuracy is poor, the signal is reported as unreliable and not graduated  - 
  measuring the measurement instrument comes before using it.
- **Timing leakage guard.** `filingDate` must be **strictly after** quarter T's end and
  **on or before** the T+1 prediction point. Asserted in code, not assumed. A Q1 earnings
  release filed in April is legitimately as-of-T information for predicting Q2.
- **Test that matters:** **incremental** lift over the graduated structured set, same
  walk-forward protocol. Coverage is ~200–400 banks of ~4,400, so it is evaluated on the
  covered subset and reported as such. **A clean "no lift → KILLED" is a valid, reportable
  result** and will be reported plainly if that is what the data says.

---

## 5. Backtest protocol

- **Expanding-window walk-forward.** Train on all rows with quarter ≤ T, predict T+1, step.
  Never random K-fold. First train window ends 2017Q4 (≥12 quarters of history).
- **All preprocessing fit inside the training window** - imputation, scaling, winsorization.
  Fold-local `sklearn` `Pipeline`, so a fold can't see its own future.
- **Baselines, always shown beside the model:** (1) base rate / always-no,
  (2) naive persistence (flagged if drawdown last quarter), (3) logistic on size alone.
- **Models:** L1 logistic (interpretable, signed weights) and `HistGradientBoosting`.
  If GBM barely beats logistic, that is the finding and it gets stated.
- **Metrics, led by the alerting use case:** `precision@k` / `recall@k` for k = top 1% and
  top 5% **per quarter** (~44 and ~220 alerts) - "if the team can work 50 alerts, what
  share are real?" Then PR-AUC, ROC-AUC, calibration curve, and per-quarter stability.
- **Out-of-time stress test:** hold out 2022Q4–2023Q4 entirely; train only on pre-2022 and
  report SVB-era performance. Expect degradation; the point is to quantify and discuss it.

## 6. Leakage & skepticism checks

1. **Shuffled-label test** - permute labels within quarter; performance must collapse to
   base rate. Anything above that is a pipeline bug.
2. **Automated as-of audit** - wired into `build`, runs every time. For each feature, assert
   the computation touches no `REPDTE > T` row, by rebuilding features on a panel truncated
   at T and requiring bit-identical output.
3. **Single-feature ablations** - standalone lift and incremental lift for every candidate.
4. **Too-good-is-a-bug rule** - any fold PR-AUC implying near-perfect separation is
   investigated as leakage before being reported as a result.
5. **≥3 documented failures in the README.** Failures are content.

## 7. Graduation framework

Scorecard per candidate, generated from the registry → `reports/signal_scorecards.md` +
README summary table: rationale, standalone precision@k lift vs base rate, incremental lift
over the graduated set, stability across folds, verdict **GRADUATE / PARK / KILL** with one
sentence of reasoning. Preregistered thresholds (set before seeing results, so the bar
isn't moved to fit the outcome):

- **GRADUATE** - positive incremental precision@5% lift, same sign in ≥70% of folds.
- **PARK** - sound rationale, inconclusive evidence, or unavailable/thin data.
- **KILL** - no standalone lift and no incremental lift, or unstable sign across folds.

## 8. Repo layout

```
src/flightrisk/{data,labels,registry,backtest,evaluate,cli}.py
                   signals/      # one module per candidate
                   extraction/   # EDGAR fetch + versioned LLM prompts
tests/            # labels, as-of, fold boundaries, mergers, registry, alert schema
reports/figures/  notebooks/ (one, exploratory only)  data/ (gitignored, small sample kept)
Makefile  pyproject.toml  EXPERIMENTS.md  PLAN.md  README.md
```

CLI: `pull` · `build` · `backtest` · `scorecards` · `score --quarter 2026Q1`.
Python 3.12 (pinned via `uv`; 3.14 is the system default but lacks some wheels), ruff, pytest.

## 9. Open questions / judgement calls

1. **`DEP` includes foreign deposits.** For the handful of banks with large foreign books,
   `DEPDOM` is arguably the cleaner target. *Decision:* label on `DEP` (matches the brief),
   carry `DEPDOM` as a robustness check and report whether the label set changes materially.
2. **Seasonality.** Q4→Q1 deposit declines are seasonal for many banks. *Decision:* report
   base rate by quarter-of-year; if seasonality is strong, add a quarter-of-year control and
   test whether it changes graduation verdicts. Not hidden in a "seasonal adjustment."
3. **−5% on total deposits is a blunt instrument** for large banks (a −5% quarter at a $200B
   bank is a crisis; at a $300M bank it's one departing municipal depositor). *Decision:*
   keep −5% as primary for comparability, report metrics split by size bucket.
4. **LLM extraction cost** on ~200 banks × ~20 quarters ≈ 4,000 docs. *Decision:* start with
   the 2021Q1–2026Q1 window and the ~150 largest covered banks, expand if lift appears.
   Cached, so no re-spend.
5. **Merger detection is as-of-today, not as-of-T.** `institutions` reflects current status;
   a bank active at T that merged in 2025 is flagged inactive now. Using that to *exclude*
   rows is mild hindsight - defensible for label hygiene (I'm removing non-events, not
   adding predictive power) but it is a caveat, and it will be stated in Limitations rather
   than buried.

## 10. Phases - all complete

1. ✅ Verify API, fields, coverage → this document
2. ✅ Data pull + labels + base rates (shown before any model was fitted)
3. ✅ Sanity EDA · 4. ✅ Baselines · 5. ✅ Structured candidates · 6. ✅ Walk-forward
7. ✅ EDGAR extraction + 30-doc validation set · 8. ✅ Incremental test · 9. ✅ Scorecards
10. ✅ CLI polish · 11. ✅ Report

Two phases went differently than planned, and the differences are documented rather than
smoothed over:

* **§4's unstructured leg was built and KILLED** - but on *coverage power*, not on a
  demonstrated null. Two of four extracted flags validate poorly (precision 0.40 / 0.46
  against 30 blind hand labels), and the covered evaluation window contains 6 events, so
  the null cannot be separated from a lack of power. See the README's text section.
* **Two falsification experiments were added** beyond this plan (`flightrisk falsify`),
  and one of them **corrected a claim an earlier draft of the README made** about why the
  linear model collapses out-of-time. See EXPERIMENTS.md phase 7.

Retrospective on this plan: §1's field verification and §9's open questions were the two
highest-value sections. Every open question got a measured answer, and three of the five
turned out to matter (seasonality was real but secondary; the size skew forced the
stratified alert list; the merger-hindsight caveat survived into Limitations).
