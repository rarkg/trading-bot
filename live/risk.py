"""RiskManager: Kelly sizing, per-asset DD limits, max position size.

Ensures no single trade risks more than 2% of account equity.
"""

from dataclasses import dataclass, field
from typing import Optional


# Per-asset maximum drawdown limits (fraction of equity)
DEFAULT_DD_LIMITS: dict[str, float] = {
    "BTC": 0.25,
    "ETH": 0.25,
    "SOL": 0.25,
    "LINK": 0.25,
}

MAX_RISK_PER_TRADE = 0.02  # 2% of account equity


@dataclass
class AssetRiskState:
    """Tracks drawdown state for a single asset."""
    peak_equity: float = 0.0
    current_equity: float = 0.0
    halted: bool = False


class RiskManager:
    """Manages position sizing and per-asset drawdown limits.

    Uses Kelly criterion for optimal sizing, capped at 2% account risk per trade.
    """

    def __init__(
        self,
        dd_limits: Optional[dict] = None,
        max_risk_per_trade: float = MAX_RISK_PER_TRADE,
    ) -> None:
        """Initialize RiskManager.

        Args:
            dd_limits: Per-asset max drawdown as fraction (e.g. 0.25 = 25%).
                       Defaults to 25% for all assets.
            max_risk_per_trade: Max fraction of equity risked per trade. Default 0.02.
        """
        self.dd_limits = dd_limits or dict(DEFAULT_DD_LIMITS)
        self.max_risk_per_trade = max_risk_per_trade
        self._asset_state: dict[str, AssetRiskState] = {}

    def kelly_size(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        capital: float,
    ) -> float:
        """Calculate position size in USD using Kelly criterion.

        Kelly fraction = W - (1-W)/R where W=win_rate, R=avg_win/avg_loss.
        Result is capped at max_risk_per_trade * capital.

        Args:
            win_rate: Historical win rate (0.0 to 1.0).
            avg_win: Average winning trade in USD.
            avg_loss: Average losing trade in USD (positive number).
            capital: Current account equity in USD.

        Returns:
            Position size in USD. Returns 0 if Kelly fraction is non-positive.

        Raises:
            ValueError: If inputs are out of valid range.
        """
        if not 0.0 <= win_rate <= 1.0:
            raise ValueError(f"win_rate must be between 0 and 1, got {win_rate}")
        if avg_loss <= 0:
            raise ValueError(f"avg_loss must be positive, got {avg_loss}")
        if avg_win <= 0:
            raise ValueError(f"avg_win must be positive, got {avg_win}")
        if capital <= 0:
            return 0.0

        win_loss_ratio = avg_win / avg_loss
        kelly_fraction = win_rate - (1.0 - win_rate) / win_loss_ratio

        if kelly_fraction <= 0:
            return 0.0

        # Cap at max risk per trade
        capped = min(kelly_fraction, self.max_risk_per_trade)
        return round(capped * capital, 2)

    def check_drawdown(self, asset: str, current_equity: float) -> bool:
        """Check if an asset has breached its drawdown limit.

        Updates peak equity tracking and halts trading if DD limit exceeded.

        Args:
            asset: Asset name ("BTC", "ETH", etc.).
            current_equity: Current equity allocated to this asset.

        Returns:
            True if trading is allowed, False if halted due to drawdown.
        """
        asset = asset.upper()
        if asset not in self._asset_state:
            self._asset_state[asset] = AssetRiskState(
                peak_equity=current_equity,
                current_equity=current_equity,
            )
            return True

        state = self._asset_state[asset]
        state.current_equity = current_equity

        if current_equity > state.peak_equity:
            state.peak_equity = current_equity
            state.halted = False
            return True

        if state.peak_equity > 0:
            dd = (state.peak_equity - current_equity) / state.peak_equity
            limit = self.dd_limits.get(asset, 0.25)
            if dd >= limit:
                state.halted = True
                return False

        return not state.halted

    def max_position_size(self, capital: float, stop_distance_pct: float) -> float:
        """Calculate maximum position size given a stop distance.

        Ensures the loss at stop-out doesn't exceed max_risk_per_trade * capital.

        Args:
            capital: Current account equity in USD.
            stop_distance_pct: Distance to stop loss as fraction (e.g. 0.02 = 2%).

        Returns:
            Maximum position size in USD.
        """
        if stop_distance_pct <= 0 or capital <= 0:
            return 0.0
        max_loss = self.max_risk_per_trade * capital
        return round(max_loss / stop_distance_pct, 2)

    def reset_asset(self, asset: str) -> None:
        """Reset drawdown tracking for an asset.

        Args:
            asset: Asset name to reset.
        """
        self._asset_state.pop(asset.upper(), None)
