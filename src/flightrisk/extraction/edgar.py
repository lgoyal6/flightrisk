"""SEC EDGAR fetch: Item 2.02 ("Results of Operations") 8-K filings and their exhibits.

**Why 8-K Item 2.02 and not transcripts.** Earnings-call transcripts are paywalled behind
vendors. Item 2.02 8-Ks are the earnings press release itself -- free, complete, timestamped,
and filed by every US bank holding company. The brief's fallback is taken immediately rather
than fighting a paywall.

**Why enumerate rather than search.** EDGAR full-text search for "deposit outflows" returns
1,089 hits *because those documents mention it* -- conditioning the sample on the outcome. So
this enumerates every Item 2.02 filing for a fixed panel and lets the extractor decide.

**Timing.** `filing_date` is recorded on every document and asserted against the prediction
quarter in `timing.py`. A Q1 earnings release filed in April is legitimate as-of-T information
for predicting Q2; a document filed after the prediction quarter opens is not.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pandas as pd
import requests

from ..config import DATA, RAW, SEC_SUBMISSIONS, SEC_UA, quarter_index

CROSSWALK = DATA / "manual" / "edgar_crosswalk.csv"
FILING_CACHE = RAW / "edgar" / "filings"
DOC_CACHE = RAW / "edgar" / "docs"
INDEX = RAW / "edgar" / "filing_index.parquet"

# SEC asks for <=10 requests/second and a descriptive User-Agent.
#
# The throttle must be GLOBAL, not per-thread. A `time.sleep(0.12)` inside the request function
# gives 8 concurrent workers ~66 req/s, which is six times SEC's published limit -- impolite, and
# it risks a block halfway through a long run. A lock-guarded token bucket enforces the rate
# across every thread instead.
SEC_MIN_INTERVAL = 0.11
_RATE_LOCK = threading.Lock()
_LAST_REQUEST = [0.0]
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"})


def _throttle() -> None:
    """Block until at least SEC_MIN_INTERVAL has passed since the last request, process-wide."""
    with _RATE_LOCK:
        wait = SEC_MIN_INTERVAL - (time.monotonic() - _LAST_REQUEST[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST[0] = time.monotonic()


def load_crosswalk() -> pd.DataFrame:
    if not CROSSWALK.exists():
        raise FileNotFoundError(f"missing {CROSSWALK} -- see extraction/README")
    return pd.read_csv(CROSSWALK)


def _get(url: str, retries: int = 3) -> requests.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            _throttle()
            r = SESSION.get(url, timeout=60)
            r.raise_for_status()
            return r
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"SEC request failed: {url}") from last


def submissions(cik: int, refresh: bool = False) -> dict:
    FILING_CACHE.mkdir(parents=True, exist_ok=True)
    cache = FILING_CACHE / f"CIK{cik:010d}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    payload = _get(f"{SEC_SUBMISSIONS}/CIK{cik:010d}.json").json()
    cache.write_text(json.dumps(payload))
    return payload


def _rows_from_block(block: dict, cik: int) -> list[dict]:
    n = len(block.get("form", []))
    out = []
    for i in range(n):
        if block["form"][i] != "8-K":
            continue
        items = block.get("items", [""] * n)[i] or ""
        if "2.02" not in items:
            continue
        out.append(
            {
                "cik": cik,
                "accession": block["accessionNumber"][i].replace("-", ""),
                "accession_dashed": block["accessionNumber"][i],
                "filing_date": block["filingDate"][i],
                "items": items,
                "primary_doc": block["primaryDocument"][i],
            }
        )
    return out


def list_item202_filings(cik: int, refresh: bool = False) -> list[dict]:
    """Every Item 2.02 8-K for a CIK, including the paginated older archives."""
    payload = submissions(cik, refresh=refresh)
    rows = _rows_from_block(payload["filings"]["recent"], cik)
    for extra in payload["filings"].get("files", []):
        older = _get(f"{SEC_SUBMISSIONS}/{extra['name']}").json()
        rows.extend(_rows_from_block(older, cik))
    return rows


def build_index(
    start_quarter: str = "2022Q1", end_quarter: str = "2025Q4", verbose: bool = True
) -> pd.DataFrame:
    """One row per (bank, Item 2.02 filing) inside the window."""
    cw = load_crosswalk()
    lo, hi = quarter_index(start_quarter), quarter_index(end_quarter)
    rows = []
    for _, b in cw.iterrows():
        try:
            filings = list_item202_filings(int(b["cik"]))
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"  {b['bank']}: FAILED ({type(exc).__name__})")
            continue
        kept = 0
        for f in filings:
            d = pd.Timestamp(f["filing_date"])
            qi = d.year * 4 + (d.quarter - 1)
            if not (lo <= qi <= hi):
                continue
            rows.append(
                {
                    **f,
                    "cert": int(b["cert"]),
                    "bank": b["bank"],
                    "ticker": b["ticker"],
                    "filed_quarter": f"{d.year}Q{d.quarter}",
                }
            )
            kept += 1
        if verbose:
            print(f"  {b['bank'][:34]:34s} {kept:3d} Item 2.02 filings in window", flush=True)
    idx = pd.DataFrame(rows)
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    idx.to_parquet(INDEX, index=False)
    return idx


def _filing_files(cik: int, accession: str) -> list[str]:
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json"
    payload = _get(url).json()
    return [i["name"] for i in payload["directory"]["item"]]


_NARRATIVE = re.compile(
    r"[A-Za-z,;'\- ]{40,}(deposit|funding|liquidity)[A-Za-z,;'\- ]{40,}", re.IGNORECASE
)


def narrative_score(html: str) -> int:
    """How much prose *commentary* about deposits a document contains.

    Not a count of the word "deposit": a large bank's `ex99-1` is the financial supplement, where
    every deposit mention sits inside a numeric table row. Picking the first exhibit
    alphabetically handed the extractor JPMorgan's tables -- 26 deposit mentions, 30% digits, zero
    sentences -- and it correctly returned all-false. Measuring the extractor on documents with no
    extractable content would have produced "text has no lift" for the wrong reason.

    So score by deposit mentions that appear inside a run of *letters and spaces*, i.e. an actual
    sentence, and pick the highest-scoring document in the filing.
    """
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return len(_NARRATIVE.findall(text))


def fetch_document(row: pd.Series, refresh: bool = False) -> Path | None:
    """Download the most narrative document in one filing.

    Candidates are the `ex99` exhibits (press release, sometimes a supplement) plus the primary
    8-K body; whichever contains the most prose deposit commentary wins. The choice is recorded
    alongside the cached document so the selection is auditable.
    """
    DOC_CACHE.mkdir(parents=True, exist_ok=True)
    out = DOC_CACHE / f"{int(row['cik'])}_{row['accession']}.html"
    meta = out.with_suffix(".choice.json")
    if out.exists() and not refresh:
        return out
    cik, acc = int(row["cik"]), row["accession"]
    try:
        names = _filing_files(cik, acc)
    except Exception:  # noqa: BLE001
        names = []
    candidates = [n for n in names if "ex99" in n.lower() and n.lower().endswith((".htm", ".html"))]
    candidates = sorted(candidates)[:4]  # bound the fetches per filing
    if row["primary_doc"] not in candidates:
        candidates.append(row["primary_doc"])

    best, best_html, scores = None, None, {}
    for name in candidates:
        try:
            html = _get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}").text
        except Exception:  # noqa: BLE001
            continue
        s = narrative_score(html)
        scores[name] = s
        if best is None or s > scores.get(best, -1):
            best, best_html = name, html
    if best_html is None:
        return None
    out.write_text(best_html)
    meta.write_text(json.dumps({"chosen": best, "scores": scores}, indent=1))
    return out
