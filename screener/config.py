"""Central configuration for the NSE text screener.

Every tunable below can be overridden without code edits via an
optional `data/config_local.toml` (loaded once, here, at import time)
— e.g. to loosen the liquidity gate or widen SR tolerance for a
smaller or more volatile universe than Nifty 500. Only names in
`_OVERRIDABLE` are honoured; anything else in the file is flagged and
ignored rather than silently creating a new, unused setting. The
effective values are hashed (`config_hash()`) and recorded with every
screen (screen_log.jsonl + the web UI's methodology footer) — a
screen's result is only ever reproducible together with the config
that produced it, not the spec alone.
"""
import hashlib
import sys
from pathlib import Path

from .universes import DEFAULT_UNIVERSE

# ---------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
INDICATOR_STORE = DATA_DIR / "indicators.parquet"  # wide per-symbol latest snapshot
# Data layer v2 (ROADMAP Item 3) — runs side-by-side with the yfinance
# store above; nothing reads from these yet. See TECHNICAL_DESIGN.md §4a.
BHAVCOPY_STORE = DATA_DIR / "bhavcopy_prices.parquet"
CORP_ACTIONS_STORE = DATA_DIR / "corporate_actions.parquet"
DIVERGENCE_LOG = DATA_DIR / "bhavcopy_divergence.jsonl"
LOCAL_CONFIG_FILE = DATA_DIR / "config_local.toml"


# ---------------------------------------------------------------- per-universe storage (ROADMAP Item 15 Phase A)
def universe_dir(universe_id: str = DEFAULT_UNIVERSE) -> Path:
    return DATA_DIR / universe_id


def price_store(universe_id: str = DEFAULT_UNIVERSE) -> Path:
    """Long-format parquet: symbol, date, ohlcv."""
    return (PRICE_STORE if universe_id == DEFAULT_UNIVERSE
           else universe_dir(universe_id) / "prices.parquet")


def universe_file(universe_id: str = DEFAULT_UNIVERSE) -> Path:
    return (UNIVERSE_FILE if universe_id == DEFAULT_UNIVERSE
           else universe_dir(universe_id) / "universe.csv")


def benchmark_store(universe_id: str = DEFAULT_UNIVERSE) -> Path:
    return (BENCHMARK_STORE if universe_id == DEFAULT_UNIVERSE
           else universe_dir(universe_id) / "benchmark.parquet")


# Kept as plain attributes (not just the functions above) so existing
# `monkeypatch.setattr(config, "PRICE_STORE", ...)`-style test fixtures
# keep working unchanged — the functions defer to these for the default
# universe rather than recomputing the path, so patching the attribute
# still redirects everything that reads it.
PRICE_STORE = universe_dir() / "prices.parquet"
UNIVERSE_FILE = universe_dir() / "universe.csv"
BENCHMARK_STORE = universe_dir() / "benchmark.parquet"


def _migrate_legacy_nifty500_layout() -> None:
    """One-time move: before Item 15 Phase A, the (only) Nifty 500
    universe's files sat flat in `data/` (prices.parquet, nifty500.csv,
    benchmark.parquet). Every universe now gets its own `data/{id}/`
    directory. Idempotent — a no-op once the new layout exists, and
    harmless if the old layout was never there (CI, a fresh clone)."""
    legacy = {
        DATA_DIR / "prices.parquet": PRICE_STORE,
        DATA_DIR / "nifty500.csv": UNIVERSE_FILE,
        DATA_DIR / "benchmark.parquet": BENCHMARK_STORE,
    }
    moved = [old.name for old, new in legacy.items()
            if old.exists() and not new.exists()]
    if not moved:
        return
    universe_dir().mkdir(parents=True, exist_ok=True)
    for old, new in legacy.items():
        if old.exists() and not new.exists():
            old.rename(new)
    print(f"[config] migrated legacy data/ layout -> "
         f"data/{DEFAULT_UNIVERSE}/: {', '.join(moved)}", file=sys.stderr)


_migrate_legacy_nifty500_layout()

# ---------------------------------------------------------------- data
HISTORY_YEARS = 5
YF_SUFFIX = ".NS"
NIFTY500_URL = (
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
)
# Liquidity gate: 20-day median turnover (crores INR). Nifty 500 is already
# liquid, but this guards against data glitches producing near-zero volume.
MIN_MEDIAN_TURNOVER_CR = 0.5

# Staleness: refuse to screen if latest bar is older than this many
# calendar days (covers weekends + one holiday cluster).
MAX_STALENESS_DAYS = 5

# ---------------------------------------------------------------- support/resistance (sr.py)
PIVOT_K = 5
SR_LOOKBACK = 250
SR_MIN_TOUCHES = 2
SR_CLUSTER_TOL_PCT = 1.0

# ---------------------------------------------------------------- web UI
SPARK_BARS = 60
MAX_MATCHES = 100

# ---------------------------------------------------------------- indicators
EMA_PERIODS = [10, 20, 50, 100, 200]
SMA_PERIODS = [20, 50, 150, 200]  # 150 added for the Minervini "stage 2"
                                   # trend template (ROADMAP Item 9)
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14
VOL_AVG_PERIOD = 20
BB_PERIOD, BB_STD = 20, 2.0

# ---------------------------------------------------------------- parser
ANTHROPIC_MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------- user overrides
_OVERRIDABLE = (
    "MIN_MEDIAN_TURNOVER_CR", "MAX_STALENESS_DAYS",
    "PIVOT_K", "SR_LOOKBACK", "SR_MIN_TOUCHES", "SR_CLUSTER_TOL_PCT",
    "SPARK_BARS", "MAX_MATCHES",
)


def _load_local_overrides() -> dict:
    if not LOCAL_CONFIG_FILE.exists():
        return {}
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
    with open(LOCAL_CONFIG_FILE, "rb") as fh:
        data = tomllib.load(fh)
    applied = {}
    for key, value in data.items():
        if key not in _OVERRIDABLE:
            print(f"[config] ignoring unknown override {key!r} in "
                  f"{LOCAL_CONFIG_FILE} (not one of {_OVERRIDABLE})",
                  file=sys.stderr)
            continue
        globals()[key] = value
        applied[key] = value
    return applied


LOCAL_OVERRIDES = _load_local_overrides()


def config_hash() -> str:
    """Short, stable hash of the effective value of every overridable
    tunable — logged with each screen so a result stays traceable to
    the config that produced it even after config_local.toml changes."""
    payload = "|".join(f"{k}={globals()[k]}" for k in _OVERRIDABLE)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]
