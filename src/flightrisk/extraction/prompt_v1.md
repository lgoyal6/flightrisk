You are extracting structured deposit-funding signals from a US bank's quarterly earnings press release (SEC Form 8-K, Item 2.02).

Read the document and answer ONLY about what management says regarding **deposits and funding**. Ignore loan growth, credit quality, capital ratios, and everything else unless it is framed as affecting deposits or funding.

Return a single JSON object and nothing else - no prose, no markdown fence, no explanation:

{
  "deposit_pressure_mentioned": true | false,
  "funding_concern_tone": 0 | 1 | 2 | 3,
  "explicit_outflow_language": true | false,
  "explicit_inflow_language": true | false,
  "evidence_quote": "<= 200 characters, verbatim from the document, or empty string"
}

Field definitions - apply these literally:

- `deposit_pressure_mentioned`: true if management discusses ANY difficulty, competition, cost pressure, or strain related to gathering or retaining deposits. Competing for deposits, rising deposit costs, deposit repricing, and migration to higher-yield products all count. Routine reporting of a deposit balance with no commentary does NOT count.

- `funding_concern_tone`: how concerned management sounds about funding, judged only on deposits/funding language.
  - 0 = no funding commentary at all, or purely positive
  - 1 = neutral/factual mention of deposit costs or competition, no worry conveyed
  - 2 = clear acknowledgement of pressure, headwinds, or margin compression from funding
  - 3 = explicit concern about deposit retention, outflows, or liquidity

- `explicit_outflow_language`: true ONLY if the document states deposits DECREASED, declined, ran off, or were withdrawn. Requires an actual directional claim about deposits falling - not merely "competition for deposits."

- `explicit_inflow_language`: true ONLY if the document states deposits GREW, increased, or that the bank attracted new deposits.

- `evidence_quote`: the single most relevant verbatim sentence fragment supporting your answers. Empty string if there is no deposit/funding commentary.

Both outflow and inflow may be true (some segments grew while others shrank). Both may be false.

Judge only what the document says. Do not infer from your own knowledge of the bank, the date, or macro conditions. If the document is truncated or unreadable, set every boolean false, tone 0, and quote "".
