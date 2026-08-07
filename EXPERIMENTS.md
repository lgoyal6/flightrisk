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

## Phase 3 — features, registry, as-of audit

**2026-08-06 · Do any features reach forward in time?**
Test: rebuild every feature from a panel truncated at Q and require values at Q to be
bit-identical to the full-panel build. Behavioural, not a code review — a centered window, a
negative shift, or a full-sample statistic all change when the future is removed.
Result: 31 signals x 5 cutoffs, **764,398 row-comparisons, 0 mismatches**.
Decision: wired into `drawdown-radar build`, so it runs on every execution rather than on
request.

**2026-08-06 · Lags via `groupby().shift()` vs an explicit quarter-index join.**
Hypothesis: `shift(k)` is fine for trailing features.
Result: unsafe. With a gap in a bank's reporting history, `shift(1)` returns the previous
*observation*, not the previous *quarter*, silently presenting a two-quarter change as one
quarter. Same trap the label construction avoids.
Decision: `features.add_lags` joins on `qidx - k`. Two regression tests pin it.

## Phase 4 — baselines and metric choice

**2026-08-06 · Are pooled metrics inflated on this panel, and by how much?**
Hypothesis: with the base rate moving 11x (1.16% to 12.86%), a model can score well pooled by
inferring which quarter it is.
Result: **confirmed, twice, quantitatively.**
(a) The macro-only baseline (aggregate system deposit growth, constant within a quarter) has
within-quarter AUC **0.5000** and lift **0.99** — zero ranking power by construction — yet
pooled AUC **0.587** and pooled PR-AUC **1.27x** the base rate.
(b) The shuffled-label test destroys all real signal, and pooled PR-AUC *still* reads 0.0745
against a 0.0568 base rate: a 1.31x "lift" on pure noise.
Decision: within-quarter precision@k / recall@k, macro-averaged, is the headline. Pooled
metrics are reported labelled `INFLATED`.

**2026-08-06 · Baseline sweep** (28 walk-forward quarters, test-window base rate 5.68%).
random 1.05x lift@5% · persistence 1.99x · size-only 2.09x · macro-only 0.99x ·
logit L1 4.15x · **HistGB 5.91x** (precision@5% 0.298, precision@1% 0.456, AUC_wq 0.805).
Decision: HistGB is the primary model. It beats logistic **decisively, not marginally**
(0.298 vs 0.209), which the next entry explains.

**2026-08-06 · Why does the persistence baseline have AUC 0.406 — worse than random — while
its top 1% has lift 3.49?**
Hypothesis: a sign error.
Result: **No — the relationship is U-shaped.** Next-quarter drawdown rate against this
quarter's deposit growth: <-10% -> 18.55% (3.30x base); 0..+2% -> 3.24% (0.58x, the safest
zone); >+10% -> **16.86% (3.00x)**. Banks that just took in large inflows are nearly as
likely to have a drawdown as banks that just lost deposits — lumpy money is transient money.
Ranking by decline alone therefore inverts across the middle of the distribution.
Decision: report it prominently. It explains the GBM's margin (an L1 logistic cannot represent
a U-shape) and it is the most transferable finding here: an account that just received a
funding round is a drawdown risk, not a safe one.

**2026-08-06 · Does knowing the macro regime add anything to the book?**
Result: No. HistGB+macro 6.03x vs HistGB 5.91x lift@5%; AUC 0.809 vs 0.805.
Decision: macro covariates stay registered as tested controls, excluded from the model. The
bank's own funding structure already encodes the cycle.

## Phase 5 — skepticism checks

**2026-08-06 · Shuffled-label test.** Labels permuted *within* quarter, preserving each
quarter's base rate (a global shuffle also destroys the regime structure, making the test far
easier to pass). Result: lift@5% **1.007**, AUC_wq **0.503** vs 5.91 / 0.805 on real labels.
**PASS.** Decision: assertion in the pipeline — the run fails if it ever stops collapsing.

**2026-08-06 · Out-of-time SVB stress test.** Trained only on `T <= 2021Q3`, no refits,
predicting events 2022Q4–2023Q4 (window base rate 9.90%).
Result: HistGB precision@1% **0.532** (5.76x), precision@5% 0.363 (3.84x), AUC 0.734 — degraded
from 0.805/5.91 but still clearing every baseline. **logit L1 collapsed to 1.56x lift, worse
than the size-only baseline (2.05x).**
Decision: report the degradation honestly, and the linear/tree divergence — a rate cycle it had
never seen broke the linear coefficients while ordinal tree splits held.

**2026-08-06 · Manual-exclusion sensitivity** (the 6 hand-adjudicated structure events).
Result: Δprecision@5% **+0.002**, ΔAUC **+0.0002**.
Decision: **the labelling debate is closed** — results are insensitive to those rows.

**2026-08-06 · Severity-tier consistency.**
Result: severe tier base rate 1.13%, precision@1% 0.285 (26.9x lift), AUC 0.908. A score
trained on -5% ranks -10% events at AUC **0.901**, lift 24.0 at k=1%.
Decision: -5% stays primary (the severe tier's ~2,100 events over 40 quarters are too thin for
stable walk-forward evaluation); per-signal severe-tier lift is a scorecard stability column.

**2026-08-06 · Size stratification.** Unstratified top-k is a small-bank list (3,287 of 4,352
banks are under $1B).
Result: HistGB beats size-only *inside every band* — <$1B 5.83x vs 2.02x; $1B–$10B **6.37x vs
1.07x** (size does essentially no work there); >$10B **7.21x vs 2.83x**, the highest lift of
the three and the band holding the events that matter.
Decision: the alert list is quota'd and percentiled per band. Caveat logged: at k=1% the >$10B
band is ~1.6 alerts/quarter, too thin to read as stable.

## Phase 6 — ablations and graduation

**2026-08-06 · A "null" tripwire fired. Framework broken, or bad tripwire?**
`dumb_state_alpha_rank` (alphabetical rank of the bank's state), registered as deliberately
meaningless, scored standalone lift **1.75**, AUC 0.601, above random in **100% of folds**.
Result: **bad tripwire, not a broken framework.** `.cat.codes` produces a state *identifier*,
and a tree splits it into arbitrary subsets of states without caring that the ordering is
alphabetical — so it encodes regional deposit dynamics. State drawdown rates run **1.68% (ME)
to 15.13% (NV)**, a 9x spread.
Decision: the general lesson is that **in a panel, any stable entity identifier is non-null**,
because it lets a model memorise entity-level base rates; a genuinely null feature must be
random per ROW. `state_identity` is now a registered control *excluded from the model*
(geography is real but it is a confounder, not a leading indicator of one customer's
behaviour), and `dumb_row_noise` — seeded pseudorandom per bank-quarter — replaced it. It
scores lift **0.991**, correctly null.

**2026-08-06 · Do the remaining null tripwires stay dead?**
Result: `dumb_cert_parity` 0.807, `dumb_asset_digit_sum` 1.053, `dumb_row_noise` 0.991 — all at
or below random. Adding all three to the model moves lift@5% by 0.02 (5.93 vs 5.91).
Decision: tripwire mechanism validated.

**2026-08-06 · Standalone vs incremental lift diverge sharply.**
Result: strongest standalone are `dep_growth_decel_4q` (4.37x), `dep_growth_1q` (4.11x),
`dep_growth_vol_4q` (3.29x), `prior_drawdown_count_4q` (3.14x). But several strong standalone
signals are **redundant**: `asset_dep_divergence` (2.54x standalone, **-0.03** incremental),
`noninterest_dep_share` (2.46x, **-0.06**), `nim_level` (2.25x, -0.05). Largest incremental
contributors are `ltd_level` (+0.159), `dep_growth_decel_4q` (+0.133), `uninsured_dep_share`
(+0.100), `dep_growth_vol_4q` (+0.099).
Decision: incremental lift drives the verdict; standalone alone would have graduated a set of
mutually-redundant funding-mix ratios.

**2026-08-06 · Graduation verdicts.** 8 GRADUATED, 18 PARKED, 5 KILLED.
Graduated: `dep_growth_decel_4q` (+0.133 incremental), `ltd_level` (+0.159),
`uninsured_dep_share` (+0.100), `dep_growth_vol_4q` (+0.099), `prior_drawdown_count_4q`
(+0.090), `log_assets` (+0.082), `unrealized_afs_loss_to_eq` (+0.072), `dep_growth_1q` (+0.050).
Killed: 3 null tripwires + both macro covariates — every kill correct by construction.
Notable: both SVB mechanisms (uninsured share, unrealized AFS losses) graduated **and** rank
the -10% tier (severe lift 5.21 and 1.64), so they read funding fragility rather than one
threshold. `log_assets` earns its place as a control, which is worth stating plainly rather
than hiding.

**2026-08-06 · Random baseline was not reproducible.**
Result: **Bug.** The baseline seeded `np.random.default_rng` from `hash(quarter_string)`, and
Python salts string hashing per process (`PYTHONHASHSEED`), so lift@5% moved 1.05 -> 0.99
between two runs of identical code.
Decision: seed from `zlib.crc32` instead. Verified identical across processes under
`PYTHONHASHSEED=random`. A baseline that is not reproducible is not a baseline.

**2026-08-06 · Scoring population did not match the training population.**
Result: **Bug** found by reading the generated alert list: `GENERATIONS COMMUNITY BANK`
($0.1M deposits) ranked in the top 25 under $1B. The `score` path rebuilt features from the raw
panel and skipped the training-time eligibility filters, so a -5% rule that is meaningless at
$100k scale was being applied.
Decision: `_unlabelled_quarter` now mirrors every training filter (charter class, $10M floor,
min history, de novo). 118 of 4,352 banks are excluded from scoring for 2026Q1.

**2026-08-06 · Reason strings contradicted the data.**
Result: **Bug.** BNY Mellon's top reason read "deposits already fell 26.3% last quarter" when
deposits had *risen* 26.3% ($332.4B -> $419.7B). Directional verbs were hardcoded and the sign
was ignored; the same bug printed "losses equal -8.4% of equity" and called a securities mark
that improved by 746% of equity "deteriorated."
Decision: every directional template is now a `(negative, positive)` pair selected by sign. The
corrected wording is also the more informative one, since a large inflow genuinely is the risk
signal (see the U-shape). The explanation layer is the one part of the system no metric checks,
which makes it the easiest place for a bug to survive.

---

## Phase 7 — falsification

**2026-08-06 · Recover the logit: was the nonlinearity the alpha?**
Hypothesis: logit L1's collapse (in-sample 4.15x vs HistGB 5.91x; out-of-time 1.56x vs 3.84x) is a
*representation* failure, not a model-class failure — it cannot encode the U-shaped deposit-growth
relationship. Test: quantile-bin the growth family into 9 one-hot bins (edges fitted inside each
training fold only), plus a cubic-spline variant, plus a bin-everything variant. Same folds,
nothing else changed.

Result — **yes, but not in the way the hypothesis framed it:**

| model | lift@5% | AUC_wq | OOT lift | gap closed: lift / AUC / OOT |
|---|---|---|---|---|
| logit L1 (linear) | 4.147 | 0.7250 | 1.563 | 0% / 0% / 0% |
| + binned growth (9 bins) | 5.020 | 0.7591 | 1.598 | **50% / 43% / 1.5%** |
| + spline growth | 4.714 | 0.7562 | 1.598 | 32% / 39% / 1.5% |
| + binned, ALL signals | 5.325 | 0.7850 | **3.492** | **67% / 75% / 85%** |
| HistGB (ceiling) | 5.908 | 0.8051 | 3.843 | 100% |

Two distinct findings. (a) Binning *just* the growth family closes about half the in-sample gap —
so the U-shape is worth roughly half the GBM's in-sample edge, and the rest is nonlinearity
elsewhere. (b) Binning growth closes only **1.5%** of the out-of-time gap, but binning **every**
signal closes **85%** of it.

Decision: **the nonlinearity was the alpha** — write that into the README. It also **corrects an
earlier claim**: I had explained the linear model's SVB-era collapse as "a rate cycle broke its
coefficients while ordinal tree splits held", implying a model-class advantage. The real mechanism
is representational — *continuous* linear coefficients are fragile under distribution shift and
*binned ordinal* ones are robust, whichever model consumes them. A fully-binned logistic recovers
most of the GBM's regime robustness while staying a readable linear model with signed
per-bin weights, which is a genuinely useful thing to know for a signals team.

**2026-08-06 · Unseen-entity falsification: structure or roster?**
Hypothesis to attack: HistGB partially memorises entity-level base rates through combinations of
quasi-stable per-bank ratios — the generalised version of the state-code lesson. Test: split each
test quarter's banks by whether the model had ever seen that CERT in training, and separately by
whether it had ever seen that CERT have a positive label. Scores converted to within-quarter
percentile before pooling, so per-quarter ranking discipline survives the pooling.

Result:

| cut | cohort | n | events | lift@5% | AUC |
|---|---|---|---|---|---|
| entity never in training | yes | **72** | **5** | 0.000 | 0.418 |
| entity never in training | no | 129,604 | 7,361 | 5.224 | 0.784 |
| no prior positive label | yes | 63,175 | 1,551 | **4.539** | **0.731** |
| no prior positive label | no | 66,501 | 5,815 | 4.126 | 0.750 |

The first-appearance cohort is **too thin to be conclusive** — 72 rows and 5 events, a median of 3
banks per test quarter (the 5-observation history requirement means almost nobody is genuinely new).
Its lift of 0.000 is a small-sample artifact, not a finding, and is reported as such rather than as
evidence either way.

The secondary cut has power, and it **refutes the hypothesis**: on 63,175 bank-quarters where the
bank had never once had a positive label in training, lift is **4.54x** — slightly *higher* than on
banks with a prior positive (4.13x), with AUC essentially flat (0.731 vs 0.750).
Decision: the signal is structural, not a roster. Recorded in the README limitations with the
first-appearance caveat stated plainly, so the thin cohort is not mistaken for a clean result.

---

## Phase 8 — unstructured leg (SEC 8-K Item 2.02)

**2026-08-06 · Are earnings-call transcripts obtainable?**
Result: paywalled behind vendors. Decision: take the 8-K Item 2.02 fallback immediately rather
than fight it. Item 2.02 *is* the earnings press release — free, complete, timestamped, and filed
by every US bank holding company.

**2026-08-06 · Universe.** 50 largest FDIC banks that are SEC filers, matched CERT → NAMEHCR →
`company_tickers.json` → CIK, SIC verified via the submissions API, hand-checked into
`data/manual/edgar_crosswalk.csv`. Every one is **>$10B in assets** — deliberately the stratum
where structured lift is already highest (7.21x) and where filings exist. 836 Item 2.02 filings,
2022Q1–2025Q4. Six foreign private issuers (TD, HSBC, UBS, RBC, Barclays) correctly showed **zero**
Item 2.02 filings — they file 6-K/20-F — and were dropped.

**2026-08-06 · Sample construction: search or enumerate?**
EDGAR full-text search for "deposit outflows" returns 1,089 hits *because those documents mention
it* — conditioning the sample on the outcome. Decision: enumerate every Item 2.02 filing for the
fixed panel and let the extractor decide.

**2026-08-06 · Document selection was measuring nothing (bug).**
Taking the first `ex99` exhibit alphabetically handed the extractor JPMorgan's **financial
supplement**: 26 mentions of "deposit", every one inside a numeric table row, 30% digits, zero
sentences. The extractor correctly returned all-false. **Had this stood, the run would have
concluded "text has no incremental lift" for an entirely wrong reason** — a measurement artifact
dressed as a result.
Decision: score each candidate document for *prose* deposit commentary (a deposit mention inside a
run of letters, i.e. an actual sentence) and keep the most narrative one; write the choice next to
each cached document so selection is auditable. Northern Trust then yields pressure=True, tone=2,
outflow=True with a verbatim supporting quote.

**2026-08-06 · SEC rate limit was being violated 6x over (bug).**
`time.sleep(0.12)` inside the request function is per-thread, so 8 workers hit ~66 req/s against a
published 10 req/s ceiling. Decision: lock-guarded global token bucket. Also split fetch from
extraction — interleaving made every LLM call queue behind a rate-limited HTTP fetch, projecting
~98 minutes for 836 filings.

**2026-08-06 · Extraction accuracy vs 30 blind hand labels. THE DECISIVE RESULT.**
Labels were recorded by reading the document passages with the extractor's own answer and predicted
stratum **withheld** — including them would anchor the labeller on the prediction being graded and
the resulting "agreement" would measure nothing.

| flag | positives / 30 | precision | recall | accuracy |
|---|---|---|---|---|
| `explicit_inflow_language` | 13 | **0.81** | **1.00** | **0.90** |
| `explicit_outflow_language` | 6 | 0.40 | **1.00** | 0.70 |
| `deposit_pressure_mentioned` | 11 | **0.46** | 0.55 | 0.60 |
| `funding_concern_tone` | 14 | — | — | 0.53 exact, **0.87 within-1**, MAE 0.60 |

Result: **two of four flags are unreliable.** `explicit_inflow_language` is trustworthy.
`explicit_outflow_language` catches every real one (recall 1.00) but over-triggers badly (9 false
positives). `deposit_pressure_mentioned` is close to a coin flip on a 37%-positive base. Tone is
noisy exactly but usable ordinally (87% within one level).

Diagnosis of the disagreements — 7 over-triggers, 5 misses — and the notes column says most sit on
genuine **construct** ambiguity, not model error: a metrics table showing rising cost of deposits
with no commentary; a deposit decline attributed to seasonal client tax payments; an SVB-era
deposit-diversity disclosure framed as *strength*. A careful second human would plausibly disagree
with me on several. Inter-rater reliability is the ceiling here and I have one rater, so 0.46
precision is partly a statement about the construct's fuzziness.

Decision: report the accuracy prominently and **carry the caveat into the verdict**. With two flags
this noisy, a null incremental result cannot distinguish "text carries no signal beyond the balance
sheet" from "this extractor is too noisy to detect it." Reporting a clean KILL without that
distinction would be overclaiming. The incremental test still runs — it is just not the last word.
