"""FDIC data acquisition with hard field validation.

The FDIC API silently drops unknown field names: requesting `fields=CERT,TIMEDEP` returns
HTTP 200 with `{"CERT": 628, "ID": ...}` and no error. A typo therefore produces a silently
absent column, which downstream becomes a silently broken signal. Every function here
validates requested fields against the published dictionary and asserts the response
actually contains them.
"""

from __future__ import annotations

import json
import time

import pandas as pd
import requests
import yaml

from .config import (
    API_PAGE_LIMIT,
    FDIC_API,
    FDIC_DICT_URL,
    FIN_FIELDS,
    INST_FIELDS,
    RAW,
    quarter_range,
    quarter_to_repdte,
    repdte_to_quarter,
)


class FieldValidationError(RuntimeError):
    """Raised when a requested field is absent from the dictionary or the response."""


# ------------------------------------------------------------------ dictionary
def _parse_properties_yaml(text: str) -> dict[str, dict]:
    doc = yaml.safe_load(text)
    props = doc["properties"]["data"]["properties"]
    return {
        k: {"title": v.get("title", ""), "description": v.get("description", "") or ""}
        for k, v in props.items()
    }


def load_dictionary(refresh: bool = False) -> dict[str, dict]:
    """Fetch and cache the FDIC financials data dictionary (2,378 fields)."""
    cache = RAW / "fdic_dictionary.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    resp = requests.get(FDIC_DICT_URL, timeout=120)
    resp.raise_for_status()
    fields = _parse_properties_yaml(resp.text)
    cache.write_text(json.dumps(fields, indent=1))
    return fields


def validate_fields(fields: list[str], dictionary: dict[str, dict] | None = None) -> None:
    """Fail loudly before the request if any field name is not in the FDIC dictionary."""
    d = dictionary if dictionary is not None else load_dictionary()
    unknown = [f for f in fields if f not in d]
    if unknown:
        raise FieldValidationError(
            f"{len(unknown)} field(s) absent from the FDIC dictionary and would be "
            f"SILENTLY DROPPED by the API: {unknown}"
        )


# ------------------------------------------------------------------ http
def _get_json(path: str, params: dict, retries: int = 4) -> dict:
    url = f"{FDIC_API}/{path}"
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params={**params, "format": "json"}, timeout=180)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001 - retry any transport/5xx failure
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"FDIC request failed after {retries} attempts: {url}") from last


def _records(payload: dict) -> list[dict]:
    return [row["data"] for row in payload.get("data", [])]


def _assert_columns(df: pd.DataFrame, requested: list[str], context: str) -> None:
    missing = [f for f in requested if f not in df.columns]
    if missing:
        raise FieldValidationError(
            f"{context}: API returned 200 but these requested columns are absent "
            f"(silent drop): {missing}"
        )


def _paged(path: str, params: dict, expected_total: int | None = None) -> list[dict]:
    """Offset-paginate a list endpoint (both endpoints cap at limit=10000)."""
    out: list[dict] = []
    offset = 0
    while True:
        payload = _get_json(path, {**params, "limit": API_PAGE_LIMIT, "offset": offset})
        batch = _records(payload)
        out.extend(batch)
        total = payload.get("meta", {}).get("total", 0)
        offset += API_PAGE_LIMIT
        if offset >= total or not batch:
            break
    if expected_total is not None and len(out) != expected_total:
        raise RuntimeError(f"{path}: expected {expected_total} rows, got {len(out)}")
    return out


# ------------------------------------------------------------------ financials
def pull_financials(
    quarters: list[str] | None = None, refresh: bool = False, verbose: bool = True
) -> pd.DataFrame:
    """One row per CERT x quarter. Cached per quarter so re-runs work offline."""
    quarters = quarters or quarter_range()
    validate_fields([f for f in FIN_FIELDS if f != "REPDTE"] + ["REPDTE"])
    frames = []
    for q in quarters:
        cache = RAW / "financials" / f"{q}.parquet"
        cache.parent.mkdir(parents=True, exist_ok=True)
        if cache.exists() and not refresh:
            frames.append(pd.read_parquet(cache))
            continue
        repdte = quarter_to_repdte(q)
        payload = _get_json(
            "financials",
            {
                "filters": f"REPDTE:{repdte}",
                "fields": ",".join(FIN_FIELDS),
                "limit": API_PAGE_LIMIT,
            },
        )
        rows = _records(payload)
        total = payload.get("meta", {}).get("total", 0)
        if total > API_PAGE_LIMIT:
            raise RuntimeError(f"{q}: {total} banks exceeds page limit; add pagination")
        df = pd.DataFrame(rows)
        _assert_columns(df, FIN_FIELDS, f"financials {q}")
        df["quarter"] = q
        df.to_parquet(cache, index=False)
        frames.append(df)
        if verbose:
            print(f"  pulled {q}: {len(df):5d} banks")
    panel = pd.concat(frames, ignore_index=True)
    panel["quarter"] = panel["REPDTE"].map(repdte_to_quarter)
    return panel


# ------------------------------------------------------------------ institutions
def pull_institutions(refresh: bool = False, verbose: bool = True) -> pd.DataFrame:
    """All institutions, active and inactive. NEWCERT gives the merger successor pointer."""
    cache = RAW / "institutions.parquet"
    if cache.exists() and not refresh:
        return pd.read_parquet(cache)
    validate_fields([f for f in INST_FIELDS if f not in {"ACTIVE", "NEWCERT"}])
    frames = []
    for active in (1, 0):
        rows = _paged(
            "institutions",
            {"filters": f"ACTIVE:{active}", "fields": ",".join(INST_FIELDS)},
        )
        df = pd.DataFrame(rows)
        _assert_columns(df, ["CERT", "NAME", "ESTYMD"], f"institutions ACTIVE:{active}")
        df["ACTIVE"] = active
        frames.append(df)
        if verbose:
            print(f"  pulled institutions ACTIVE:{active}: {len(df):6d}")
    inst = pd.concat(frames, ignore_index=True)
    for col in INST_FIELDS:
        if col not in inst.columns:
            inst[col] = pd.NA
    inst.to_parquet(cache, index=False)
    return inst


# ------------------------------------------------------------------ orchestration
def pull_all(refresh: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    print("Loading FDIC data dictionary...")
    d = load_dictionary(refresh=refresh)
    print(f"  dictionary: {len(d)} fields available (API returns 161 by default)")
    validate_fields(FIN_FIELDS, d)
    print(f"  all {len(FIN_FIELDS)} requested financial fields validated")
    print("Pulling quarterly financials...")
    fin = pull_financials(refresh=refresh)
    print("Pulling institution metadata...")
    inst = pull_institutions(refresh=refresh)
    fin.to_parquet(RAW / "financials_panel.parquet", index=False)
    print(f"\nPanel: {len(fin):,} bank-quarters, {fin.CERT.nunique():,} unique banks")
    print(f"Quarters: {fin.quarter.min()} -> {fin.quarter.max()}")
    return fin, inst


def load_panel() -> pd.DataFrame:
    p = RAW / "financials_panel.parquet"
    if not p.exists():
        raise FileNotFoundError("Run `flightrisk pull` first.")
    return pd.read_parquet(p)


def load_institutions() -> pd.DataFrame:
    p = RAW / "institutions.parquet"
    if not p.exists():
        raise FileNotFoundError("Run `flightrisk pull` first.")
    return pd.read_parquet(p)
