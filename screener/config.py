"""Central configuration for the NSE text screener."""
from pathlib import Path

# ---------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PRICE_STORE = DATA_DIR / "prices.parquet"          # long format: symbol, date, ohlcv
INDICATOR_STORE = DATA_DIR / "indicators.parquet"  # wide per-symbol latest snapshot
UNIVERSE_FILE = DATA_DIR / "nifty500.csv"
# Data layer v2 (ROADMAP Item 3) — runs side-by-side with the yfinance
# store above; nothing reads from these yet. See TECHNICAL_DESIGN.md §4a.
BHAVCOPY_STORE = DATA_DIR / "bhavcopy_prices.parquet"
CORP_ACTIONS_STORE = DATA_DIR / "corporate_actions.parquet"
DIVERGENCE_LOG = DATA_DIR / "bhavcopy_divergence.jsonl"

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

# ---------------------------------------------------------------- indicators
EMA_PERIODS = [10, 20, 50, 100, 200]
SMA_PERIODS = [20, 50, 200]
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14
VOL_AVG_PERIOD = 20
BB_PERIOD, BB_STD = 20, 2.0

# ---------------------------------------------------------------- parser
ANTHROPIC_MODEL = "claude-sonnet-4-6"
