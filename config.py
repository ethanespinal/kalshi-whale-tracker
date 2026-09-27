"""Paper-only settings. Money is represented as integer ten-thousandths of $1."""
from dataclasses import dataclass
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Strategy gates are module constants so experiments can be changed in one
# obvious place while each Settings instance retains the values it ran with.
MAX_HOURS_TO_CLOSE = 24
MIN_WHALE_WIN_RATE = 50
MIN_WHALE_ROI = 50
MAX_ALLOWED_SLIPPAGE = 0.00
STRATEGY_MODE = "conservative"
STRATEGY_MODES = frozenset({"all_supported", "conservative", "moneyline_only"})
MIN_WHALE_SCORE_IMPROVEMENT = 8.0
MAX_REVERSAL_COST = 5.00


@dataclass(frozen=True)
class Settings:
    database: Path = ROOT / "paper.sqlite3"
    starting_cash: int = 100_000_000  # $10,000
    fixed_stake: int = 250_000         # $25 premium per qualifying alert
    max_exposure: int = 20_000_000     # $2,000 open premium (20% of bankroll)
    max_open_positions: int = 80
    max_allowed_slippage: float = MAX_ALLOWED_SLIPPAGE  # executable minus whale, dollars
    max_alert_age: int = 300       # seconds, including time in queue
    cache_ttl: int = 120
    request_interval: float = 0.6
    max_pages: int = 3
    max_hours_to_close: float = MAX_HOURS_TO_CLOSE
    min_whale_win_rate: float = MIN_WHALE_WIN_RATE
    min_whale_roi: float = MIN_WHALE_ROI
    strategy_version: str = "v1.10"
    strategy_mode: str = STRATEGY_MODE
    min_whale_score_improvement: float = MIN_WHALE_SCORE_IMPROVEMENT
    max_reversal_cost: float = MAX_REVERSAL_COST
    # Leave unset to reuse the durable experiment session stored in SQLite.
    # Set explicitly only when intentionally starting a separate experiment.
    experiment_session_id: str | None = None
    heartbeat_interval: int = 5
    backend_offline_after: int = 15

    def __post_init__(self):
        for key in ("starting_cash", "fixed_stake", "max_exposure",
                    "max_alert_age", "cache_ttl", "max_pages", "max_open_positions",
                    "heartbeat_interval", "backend_offline_after"):
            if getattr(self, key) <= 0:
                raise ValueError(f"{key} must be positive")
        if (not math.isfinite(self.max_allowed_slippage) or
                self.max_allowed_slippage < 0 or self.request_interval < 0):
            raise ValueError("Allowed slippage and request interval must be nonnegative")
        if not math.isfinite(self.max_hours_to_close) or self.max_hours_to_close <= 0:
            raise ValueError("max_hours_to_close must be positive")
        if not 0 <= self.min_whale_win_rate <= 100:
            raise ValueError("min_whale_win_rate must be between 0 and 100")
        if not math.isfinite(self.min_whale_roi):
            raise ValueError("min_whale_roi must be finite")
        if (not math.isfinite(self.min_whale_score_improvement) or
                self.min_whale_score_improvement < 0):
            raise ValueError("min_whale_score_improvement must be nonnegative")
        if not math.isfinite(self.max_reversal_cost) or self.max_reversal_cost < 0:
            raise ValueError("max_reversal_cost must be nonnegative")
        if not self.strategy_version.strip():
            raise ValueError("strategy_version must not be empty")
        if self.strategy_mode not in STRATEGY_MODES:
            raise ValueError(f"strategy_mode must be one of {sorted(STRATEGY_MODES)}")
        if self.experiment_session_id is not None and not self.experiment_session_id.strip():
            raise ValueError("experiment_session_id must not be empty")
