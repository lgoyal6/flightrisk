"""Central configuration: paths, API hosts, the exact field set, and quarter helpers.

Every FDIC field named here was verified to exist in the 2,378-field data dictionary and to
be >=99% populated across 2015Q1-2026Q1. See PLAN.md section 1.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
REPORTS = ROOT / "reports"
FIGURES = REPORTS / "figures"

for _d in (RAW, PROCESSED, REPORTS, FIGURES):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- api
# NOTE: banks.data.fdic.gov/api 301-redirects here. Target the real host directly.
FDIC_API = "https://api.fdic.gov/banks"
FDIC_DICT_URL = "https://api.fdic.gov/banks/docs/risview_properties.yaml"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
# SEC requires a descriptive UA with contact info.
SEC_UA = "flightrisk research (laksh.g@gmicloud.ai)"

API_PAGE_LIMIT = 10_000  # hard cap on both /financials and /institutions

# ---------------------------------------------------------------- panel window
START_QUARTER = "2015Q1"
END_QUARTER = "2026Q1"  # latest published Call Report as of 2026-08-06

# ---------------------------------------------------------------- fields
# Deposit / funding structure -- the heart of the signal set.
FIN_FIELDS_DEPOSITS = [
    "DEP",  # total deposits (label numerator)
    "DEPDOM",  # domestic-office deposits (robustness check on the label)
    "DEPNI",  # noninterest-bearing deposits (stickiest, operational)
    "NTRTIME",  # total time deposits (rate-sensitive)
    "BRO",  # brokered deposits (hot money)
    "COREDEP",  # core deposits (FDIC's relationship-funding measure)
    "DEPINS",  # estimated insured deposits
    "DEPUNINS",  # estimated uninsured deposits (flight-risk tranche)
]
# Balance sheet.
FIN_FIELDS_BALANCE = [
    "ASSET",
    "EQ",
    "LIAB",
    "LNLSNET",  # net loans and leases
    "SC",  # total securities
    "SCAF",  # available-for-sale securities at FAIR VALUE
    "SCAA",  # available-for-sale securities at AMORTIZED COST
]
# FDIC-precomputed ratios. Kept for cross-checking our own arithmetic, not used as features.
FIN_FIELDS_RATIOS = ["LNLSDEPR", "EQV", "NIMY", "ROA", "ROE", "BROR"]
# Keys and metadata.
FIN_FIELDS_KEYS = ["CERT", "REPDTE", "NAME", "BKCLASS", "STALP", "ACTEVT"]

FIN_FIELDS = FIN_FIELDS_KEYS + FIN_FIELDS_DEPOSITS + FIN_FIELDS_BALANCE + FIN_FIELDS_RATIOS

INST_FIELDS = [
    "CERT",
    "NAME",
    "NAMEHCR",  # holding-company name -> EDGAR crosswalk
    "RSSDHCR",
    "ESTYMD",  # established date -> de novo filter
    "ENDEFYMD",  # inactive date -> exit detection
    "NEWCERT",  # merger successor pointer (97% populated on inactive banks)
    "ACTIVE",
    "BKCLASS",
    "ASSET",
    "STALP",
    "CITY",
]

# ---------------------------------------------------------------- event definition
# FDIC charter classes excluded from the panel.
#   NC = non-insured commercial bank (US branch of a foreign bank)
#   OI = insured US branch of a foreign institution
# These are wholesale funding vehicles, not retail/commercial deposit franchises. Empirically
# they run a 33.7% / 22.9% quarterly drawdown rate against a ~5.7% panel base rate: 0.3% of
# rows but 1.5% of all positives, across just 17 banks. Left in, a model learns "is this a
# foreign branch" and scores well while learning nothing about deposit behaviour.
EXCLUDED_BKCLASS = ("NC", "OI")

DRAWDOWN_THRESHOLD = -0.05  # primary event
SEVERE_THRESHOLD = -0.10  # severe tier
MIN_DEPOSITS_USD_K = 10_000  # FDIC reports $ thousands -> $10M floor
MIN_HISTORY_QUARTERS = 5  # need enough history for trailing features
DE_NOVO_QUARTERS = 8  # exclude banks younger than 8 quarters at T

# ---------------------------------------------------------------- quarter helpers
_QEND = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}


def quarter_to_repdte(q: str) -> str:
    """'2015Q1' -> '20150331'."""
    year, qtr = q.upper().split("Q")
    return f"{year}{_QEND[int(qtr)]}"


def repdte_to_quarter(repdte: str | int) -> str:
    """'20150331' -> '2015Q1'."""
    s = str(repdte)
    return f"{s[:4]}Q{(int(s[4:6]) - 1) // 3 + 1}"


def quarter_range(start: str = START_QUARTER, end: str = END_QUARTER) -> list[str]:
    """Inclusive list of quarter labels, e.g. ['2015Q1', '2015Q2', ...]."""
    sy, sq = int(start[:4]), int(start[-1])
    ey, eq = int(end[:4]), int(end[-1])
    out, y, q = [], sy, sq
    while (y, q) <= (ey, eq):
        out.append(f"{y}Q{q}")
        q += 1
        if q == 5:
            y, q = y + 1, 1
    return out


def quarter_index(q: str) -> int:
    """Monotonic integer index so quarter arithmetic and fold boundaries are unambiguous."""
    return int(q[:4]) * 4 + (int(q[-1]) - 1)


def next_quarter(q: str) -> str:
    y, qq = int(q[:4]), int(q[-1])
    return f"{y + 1}Q1" if qq == 4 else f"{y}Q{qq + 1}"
