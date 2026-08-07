"""LLM extraction with a fixed, versioned prompt and full response caching.

Reproducibility contract:
* The prompt lives in `prompt_v1.md` and its SHA-256 is recorded with every output. A prompt
  edit changes the hash, so stale outputs are detectable rather than silently mixed in.
* Every raw model response is cached to `data/raw/llm/`. Re-running is a cache read, so results
  are reproducible offline and the extraction is auditable after the fact.
* The model id is recorded per response, so a mixed-model run is visible in the audit trail.

Model choice: `claude-haiku-4-5` via the Claude CLI in headless mode. This is a deliberate
throughput decision -- roughly 700 documents at Opus latency does not fit the build, and the
task is short-form flag extraction rather than reasoning. Extraction accuracy is *measured*
against a hand-labelled set in `validate.py` before any signal built on it is trusted, so the
choice is falsifiable rather than assumed. `EXTRACTION_MODEL` is the single place to change it.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

from ..config import RAW

PROMPT_PATH = Path(__file__).parent / "prompt_v1.md"
PROMPT_VERSION = "v1"
EXTRACTION_MODEL = "haiku"  # resolves to claude-haiku-4-5 in the CLI
CLAUDE_BIN = "/Users/lakshgoyal/.local/bin/claude"
LLM_CACHE = RAW / "llm"
MAX_DOC_CHARS = 18_000  # press releases are long; deposit commentary is early and in the tables

FLAGS = [
    "deposit_pressure_mentioned",
    "funding_concern_tone",
    "explicit_outflow_language",
    "explicit_inflow_language",
]


def prompt_text() -> str:
    return PROMPT_PATH.read_text()


def prompt_hash() -> str:
    return hashlib.sha256(prompt_text().encode()).hexdigest()[:16]


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001 - malformed EDGAR HTML is common
        pass
    text = " ".join(p.parts)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def relevant_excerpt(text: str, limit: int = MAX_DOC_CHARS) -> str:
    """Keep the head plus any deposit/funding passages, so the prompt stays small but complete.

    Truncating blindly at N characters would cut the deposit commentary out of long releases and
    make the extractor look worse than it is -- a measurement artifact, not a model failure.
    """
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    pat = re.compile(
        r"(deposit|funding|liquidity|noninterest-bearing|brokered|outflow|inflow)", re.IGNORECASE
    )
    windows, used = [], len(head)
    for m in pat.finditer(text[limit // 2 :]):
        s = limit // 2 + m.start()
        w = text[max(0, s - 400) : s + 600]
        if used + len(w) > limit:
            break
        windows.append(w)
        used += len(w)
    return head + " […] " + " […] ".join(windows) if windows else head


def _cache_path(cik: int, accession: str) -> Path:
    return LLM_CACHE / f"{cik}_{accession}_{PROMPT_VERSION}.json"


def _call_llm(doc_text: str) -> str:
    payload = f"{prompt_text()}\n\n=== DOCUMENT ===\n{doc_text}\n=== END DOCUMENT ==="
    proc = subprocess.run(
        [CLAUDE_BIN, "-p", "--model", EXTRACTION_MODEL],
        input=payload,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr[:300]}")
    return proc.stdout.strip()


def parse_response(raw: str) -> dict:
    """Pull the JSON object out of the response, tolerating stray prose or a code fence."""
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object in response: {raw[:200]}")
    obj = json.loads(m.group(0))
    out = {
        "deposit_pressure_mentioned": bool(obj.get("deposit_pressure_mentioned", False)),
        "funding_concern_tone": int(obj.get("funding_concern_tone", 0) or 0),
        "explicit_outflow_language": bool(obj.get("explicit_outflow_language", False)),
        "explicit_inflow_language": bool(obj.get("explicit_inflow_language", False)),
        "evidence_quote": str(obj.get("evidence_quote", ""))[:300],
    }
    out["funding_concern_tone"] = max(0, min(3, out["funding_concern_tone"]))
    return out


def extract_one(row: pd.Series, doc_path: Path, refresh: bool = False) -> dict | None:
    """Extract flags for one filing. Cached: a re-run is a disk read, not a model call."""
    LLM_CACHE.mkdir(parents=True, exist_ok=True)
    cache = _cache_path(int(row["cik"]), row["accession"])
    if cache.exists() and not refresh:
        blob = json.loads(cache.read_text())
        if blob.get("prompt_hash") == prompt_hash():
            return blob
    text = relevant_excerpt(html_to_text(doc_path.read_text()))
    if len(text) < 400:
        return None
    try:
        raw = _call_llm(text)
        parsed = parse_response(raw)
    except Exception as exc:  # noqa: BLE001 - record the failure instead of losing the doc
        blob = {
            "cik": int(row["cik"]),
            "accession": row["accession"],
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "prompt_hash": prompt_hash(),
            "prompt_version": PROMPT_VERSION,
            "model": EXTRACTION_MODEL,
        }
        cache.write_text(json.dumps(blob, indent=1))
        return blob
    blob = {
        "cik": int(row["cik"]),
        "accession": row["accession"],
        "cert": int(row["cert"]),
        "bank": row["bank"],
        "filing_date": str(row["filing_date"]),
        "filed_quarter": row["filed_quarter"],
        "prompt_hash": prompt_hash(),
        "prompt_version": PROMPT_VERSION,
        "model": EXTRACTION_MODEL,
        "raw_response": raw,
        "doc_chars": len(text),
        **parsed,
    }
    cache.write_text(json.dumps(blob, indent=1))
    return blob


def fetch_all(index: pd.DataFrame, workers: int = 6, verbose: bool = True) -> dict:
    """Phase A: download documents. Bound by SEC's global rate limit, not by CPU or the LLM.

    Split from extraction on purpose: interleaving the two made every LLM call wait behind a
    rate-limited HTTP fetch, which projected to ~98 minutes for 836 filings. Separated, the fetch
    runs at SEC's ceiling and the LLM calls run at their own concurrency.
    """
    from .edgar import fetch_document

    def work(pair):
        _, row = pair
        try:
            return row["accession"], fetch_document(row)
        except Exception:  # noqa: BLE001
            return row["accession"], None

    paths = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for n, (acc, p) in enumerate(ex.map(work, list(index.iterrows())), start=1):
            if p is not None:
                paths[acc] = p
            if verbose and n % 50 == 0:
                print(f"  fetched {n}/{len(index)}", flush=True)
    return paths


def run(index: pd.DataFrame, workers: int = 10, verbose: bool = True) -> pd.DataFrame:
    """Fetch every document, then extract. Both phases are individually cached."""
    if verbose:
        print(f"phase A: fetching {len(index)} documents (SEC-rate-limited)", flush=True)
    paths = fetch_all(index, verbose=verbose)
    if verbose:
        print(f"  {len(paths)} documents on disk", flush=True)
        print(f"phase B: extracting with prompt {PROMPT_VERSION} ({prompt_hash()})", flush=True)

    rows = [(i, r) for i, r in index.iterrows() if r["accession"] in paths]

    def work(pair):
        _, row = pair
        try:
            return extract_one(row, paths[row["accession"]])
        except Exception:  # noqa: BLE001
            return None

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for n, res in enumerate(ex.map(work, rows), start=1):
            if res is not None:
                results.append(res)
            if verbose and n % 50 == 0:
                print(f"  extracted {n}/{len(rows)}", flush=True)
    df = pd.DataFrame(results)
    out = RAW / "llm_extractions.parquet"
    if not df.empty:
        df.to_parquet(out, index=False)
    return df
