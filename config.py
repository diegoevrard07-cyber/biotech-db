"""Central configuration for the Biotech Catalyst Edge Engine."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
LOGS_DIR = DATA_DIR / "logs"
SEEDS_DIR = DATA_DIR / "seeds"

for _path in (RAW_DIR, CACHE_DIR, LOGS_DIR, SEEDS_DIR):
    _path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def normalize_database_url(raw: str | None) -> str:
    """Clean a pasted DATABASE_URL and reject common Supabase copy mistakes.

    Handles surrounding quotes, a duplicated ``DATABASE_URL=`` prefix, and the
    frequent paste of an ``https://`` dashboard link (which makes libpq try to
    resolve the hostname ``https``). Returns ``""`` when unset.
    """
    if not raw:
        return ""
    value = raw.strip().strip("'").strip('"').strip()
    if value.upper().startswith("DATABASE_URL="):
        value = value.split("=", 1)[1].strip().strip("'").strip('"').strip()
    # Accidental: postgresql://user:pass@https://host/...
    if "@https://" in value:
        value = value.replace("@https://", "@", 1)
    if "@http://" in value:
        value = value.replace("@http://", "@", 1)
    if not value:
        return ""
    if value.startswith("https://") or value.startswith("http://"):
        raise RuntimeError(
            "DATABASE_URL looks like a web link (starts with https://). "
            "It must be a Postgres URI starting with postgresql://. "
            "In Supabase: Project → Connect → Transaction pooler → URI "
            "(port 6543). Replace [YOUR-PASSWORD] with your database password."
        )
    if not (value.startswith("postgresql://") or value.startswith("postgres://")):
        raise RuntimeError(
            "DATABASE_URL must start with postgresql:// "
            f"(got {value.split(':', 1)[0]!r}:…). "
            "In Supabase: Project → Connect → Transaction pooler → URI."
        )
    return value


_DATABASE_URL_RAW = os.getenv("DATABASE_URL", "")
DATABASE_URL_ERROR: str | None = None
try:
    DATABASE_URL = normalize_database_url(_DATABASE_URL_RAW)
except RuntimeError as _db_url_exc:
    DATABASE_URL = ""
    DATABASE_URL_ERROR = str(_db_url_exc)

# ---------------------------------------------------------------------------
# API endpoints (verified)
# ---------------------------------------------------------------------------
CLINICALTRIALS_API_BASE = "https://clinicaltrials.gov/api/v2/studies"
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_XBRL_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
POE_API_BASE_URL = "https://api.poe.com/v1"

SEC_USER_AGENT_PLACEHOLDER = "Biotech-Edge-Engine user@example.com"
# TODO: Replace with your real name and email; SEC requires a descriptive User-Agent.
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", SEC_USER_AGENT_PLACEHOLDER)


def check_sec_user_agent() -> None:
    """Raise if SEC_USER_AGENT is missing or still the placeholder template."""
    value = os.getenv("SEC_USER_AGENT", "").strip()
    if not value:
        raise RuntimeError(
            "SEC_USER_AGENT is not set in .env. "
            'Required format: "Firstname Lastname email@domain.com"'
        )
    if value == SEC_USER_AGENT_PLACEHOLDER or value.endswith("user@example.com"):
        raise RuntimeError(
            "SEC_USER_AGENT is still the placeholder. "
            'Set your real name and email in .env: "Firstname Lastname email@domain.com"'
        )


# ---------------------------------------------------------------------------
# GBM search terms (ClinicalTrials.gov)
# ---------------------------------------------------------------------------
GBM_SEARCH_TERMS = ["glioblastoma", "GBM", "high-grade glioma"]

# Broadened universe (Rung 2): small-cap oncology + CNS. GBM stays the flagship
# subset (see is_gbm_focused). Used to tag companies, not to gate ingestion alone.
ONCOLOGY_CNS_SEARCH_TERMS = [
    "glioblastoma",
    "glioma",
    "astrocytoma",
    "brain tumor",
    "brain cancer",
    "solid tumor",
    "carcinoma",
    "lymphoma",
    "leukemia",
    "myeloma",
    "sarcoma",
    "melanoma",
    "neuroblastoma",
    "oncology",
    "alzheimer",
    "parkinson",
    "multiple sclerosis",
    "epilepsy",
    "ALS",
    "huntington",
    "neurodegenerative",
]

# Substrings (lowercased) that mark a row as GBM-focused for is_gbm_focused.
GBM_FLAG_SUBSTRINGS = [
    "glioblastoma",
    "gbm",
    "high-grade glioma",
    "high grade glioma",
    "anaplastic astrocytoma",
    "diffuse midline glioma",
    "dipg",
]

# ---------------------------------------------------------------------------
# Universe / sizing parameters (Rung 2)
# ---------------------------------------------------------------------------
# Small-cap ceiling. Companies above this are kept but flagged out-of-universe.
SMALL_CAP_CEILING_USD = float(os.getenv("SMALL_CAP_CEILING_USD", "5000000000"))

# Price ingestion (yfinance)
PRICE_LOOKBACK_DAYS = int(os.getenv("PRICE_LOOKBACK_DAYS", "1825"))  # ~5y
YF_MAX_RETRIES = int(os.getenv("YF_MAX_RETRIES", "2"))
BENCHMARK_TICKER = os.getenv("BENCHMARK_TICKER", "XBI")  # biotech ETF for abnormal return

# Positioning / sentiment windows
RUNUP_WINDOW_DAYS = int(os.getenv("RUNUP_WINDOW_DAYS", "30"))

# Outcome labeling event window (trading days pre/post catalyst)
EVENT_WINDOW_DAYS = int(os.getenv("EVENT_WINDOW_DAYS", "5"))
# Absolute abnormal-return threshold to call a catalyst a hit/miss vs ambiguous.
OUTCOME_MOVE_THRESHOLD = float(os.getenv("OUTCOME_MOVE_THRESHOLD", "0.10"))

# Position sizing (decision scorer)
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))  # fractional Kelly
MAX_SINGLE_NAME_WEIGHT = float(os.getenv("MAX_SINGLE_NAME_WEIGHT", "0.05"))  # 5% cap

# Long-only mode: drop every short/fade (negative-weight) signal from the capped
# book. Fades are also retired at the scorer (decide_trade → avoid). Default ON;
# LONG_ONLY=0 is retained only as an emergency override and still cannot open
# shorts at the execution guard while fade weights are forced to 0.
LONG_ONLY = os.getenv("LONG_ONLY", "1") not in ("0", "false", "False")

# Portfolio construction caps (action sheet)
MAX_GROSS_LONG = float(os.getenv("MAX_GROSS_LONG", "1.0"))  # 100% long
MAX_GROSS_SHORT = float(os.getenv("MAX_GROSS_SHORT", "0.0"))  # shorts retired (was 30%)
MAX_NET = float(os.getenv("MAX_NET", "0.60"))  # unused when LONG_ONLY (net=gross long)
MAX_GBM_WEIGHT = float(os.getenv("MAX_GBM_WEIGHT", "0.25"))  # GBM names are correlated
MAX_SECTOR_WEIGHT = float(os.getenv("MAX_SECTOR_WEIGHT", "0.40"))  # per indication_category
URGENT_DAYS = int(os.getenv("URGENT_DAYS", "7"))

# Paper autopilot: sync portfolio to the capped action desk daily.
AUTOPILOT_HORIZON_DAYS = int(os.getenv("AUTOPILOT_HORIZON_DAYS", "365"))
AUTOPILOT_REBALANCE_PCT = float(
    os.getenv("AUTOPILOT_REBALANCE_PCT", "0.10")
)  # resize if |delta| > 10%

# --- Risk mitigation overlays (paper autopilot) ---
# Drawdown circuit breaker: if equity falls more than PCT below its prior peak,
# de-risk the whole book (scale targets by FACTOR, pause new opens) until it
# recovers. Catches correlated sector selloffs that per-name caps miss.
DRAWDOWN_CIRCUIT_ENABLED = os.getenv("DRAWDOWN_CIRCUIT_ENABLED", "1") not in ("0", "false", "False")
DRAWDOWN_CIRCUIT_PCT = float(os.getenv("DRAWDOWN_CIRCUIT_PCT", "0.10"))  # -10% from peak
DRAWDOWN_DERISK_FACTOR = float(os.getenv("DRAWDOWN_DERISK_FACTOR", "0.5"))  # shrink targets to 50%

# Graded drawdown tiers (act EARLIER and progressively instead of one -10% cliff).
# (drawdown_from_peak, target_scale). New opens pause once scale <= OPEN_PAUSE_SCALE.
DRAWDOWN_TIERS = [
    (0.06, 0.75),  # -6% from peak: shrink targets to 75%
    (0.10, 0.50),  # -10%: halve + pause new opens
    (0.15, 0.25),  # -15%: quarter size (deep de-risk)
]
DRAWDOWN_OPEN_PAUSE_SCALE = float(os.getenv("DRAWDOWN_OPEN_PAUSE_SCALE", "0.50"))

# Per-position hard stop (LONGS): close any long whose EOD mark is down more than
# STOP_LOSS_PCT from entry. The per-name loss protocol (portfolio breaker alone
# cannot stop a single name from bleeding).
STOP_LOSS_ENABLED = os.getenv("STOP_LOSS_ENABLED", "1") not in ("0", "false", "False")
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.15"))  # -15% from entry

# Market-regime filter: when the benchmark (XBI) closes below its N-day simple
# moving average, scale ALL target sizes down. Participate fully in an up-tape,
# run lighter gross in a down-tape: the main lever for risk-adjusted return
# vs a long-only index.
REGIME_FILTER_ENABLED = os.getenv("REGIME_FILTER_ENABLED", "1") not in ("0", "false", "False")
REGIME_SMA_DAYS = int(os.getenv("REGIME_SMA_DAYS", "20"))
REGIME_DERISK_FACTOR = float(os.getenv("REGIME_DERISK_FACTOR", "0.60"))

# Partial profit-lock (LONGS only): scale OUT a fraction of a winner when it is
# both in profit AND stretched above its short-term mean (mean reversion). Keeps
# a core position into the catalyst. Self-limiting: stops once the name reverts.
# Skips names with a catalyst within MIN_DAYS_TO_CATALYST so events can play out.
PROFIT_LOCK_ENABLED = os.getenv("PROFIT_LOCK_ENABLED", "1") not in ("0", "false", "False")
PROFIT_LOCK_GAIN_PCT = float(os.getenv("PROFIT_LOCK_GAIN_PCT", "0.20"))  # min +20% unrealized
PROFIT_LOCK_TRIM_FRACTION = float(
    os.getenv("PROFIT_LOCK_TRIM_FRACTION", "0.25")
)  # sell 25% of position
PROFIT_LOCK_ZSCORE = float(os.getenv("PROFIT_LOCK_ZSCORE", "1.5"))  # extended >=1.5 std above mean
PROFIT_LOCK_LOOKBACK_DAYS = int(os.getenv("PROFIT_LOCK_LOOKBACK_DAYS", "20"))  # mean/std window
PROFIT_LOCK_MIN_DAYS_TO_CATALYST = int(os.getenv("PROFIT_LOCK_MIN_DAYS_TO_CATALYST", "3"))

# Risk haircut (size DOWN violent names). Grounded in the event-return regression
# (returns_regression.py): direction is unpredictable, but MAGNITUDE is, and the
# strongest driver is small market cap (smaller => bigger blowups). So we shrink
# positions on tiny-cap names. This can ONLY reduce exposure (raises cash buffer),
# never increase it. Tiers: (market_cap_ceiling_usd, multiplier).
RISK_HAIRCUT_ENABLED = os.getenv("RISK_HAIRCUT_ENABLED", "1") not in ("0", "false", "False")
RISK_HAIRCUT_TIERS = [
    (100_000_000, 0.50),  # nano (< $100M): half size
    (300_000_000, 0.70),  # micro ($100-300M)
    (1_000_000_000, 0.85),  # small ($300M-$1B)
    (float("inf"), 1.00),  # >= $1B: full size
]
RISK_HAIRCUT_UNKNOWN = float(
    os.getenv("RISK_HAIRCUT_UNKNOWN", "0.70")
)  # unknown mcap: conservative

# yfinance rate limit (be polite; it is unofficial)
YF_MAX_REQUESTS_PER_SEC = float(os.getenv("YF_MAX_REQUESTS_PER_SEC", "2"))
# Hard timeout per yfinance call. yfinance has no reliable timeout and can hang
# forever on some symbols; we abandon the call after this many seconds.
YF_CALL_TIMEOUT = float(os.getenv("YF_CALL_TIMEOUT", "20"))

# ---------------------------------------------------------------------------
# Poe council bot names
# ---------------------------------------------------------------------------
COUNCIL_MODELS = {
    "mechanism_specialist": "Claude-Opus-4",
    "design_critic": "GPT-5",
    "skeptic": "Gemini-2.5-Pro",
    "synthesizer": "Claude-Opus-4",
}

POE_API_KEY = os.getenv("POE_API_KEY", "")

# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------
POE_MAX_TRIALS_PER_SEC = 3
SEC_MAX_REQUESTS_PER_SEC = 10
CACHE_TTL_HOURS = 24
CTGOV_CACHE_TTL_DAYS = 7
CTGOV_MAX_REQUESTS_PER_MIN = 50
CTGOV_CACHE_DIR = CACHE_DIR / "ctgov"
CTGOV_CACHE_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_CATALYST_TYPES = frozenset({"phase_readout", "pdufa", "advisory_committee"})
CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

# ---------------------------------------------------------------------------
# Sponsor matching
# ---------------------------------------------------------------------------
SPONSOR_FUZZY_THRESHOLD = 85


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
def preflight(*, require_sec: bool = False) -> None:
    """Fail fast with a clear message if required config is missing.

    Call at the top of every script's main(). Keeps Rung 2 batch jobs from
    failing silently halfway through.
    """
    if DATABASE_URL_ERROR:
        raise RuntimeError(DATABASE_URL_ERROR)
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set in .env. The pipeline cannot run without a database."
        )
    if require_sec:
        check_sec_user_agent()
