from __future__ import annotations
from dataclasses import dataclass
from app.models import Odds


@dataclass
class ConsensusOdds:
    bookmaker: str
    home_odds: float
    draw_odds: float
    away_odds: float
    n_bookmakers: int


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def pick_reference_odds(all_odds: list[Odds]) -> ConsensusOdds | None:
    """Build a consensus reference price from the median of every available
    bookmaker's line, instead of any single book's price.

    Comparing the model against one bookmaker - even "whichever currently
    has the lowest margin" - means a single stale or soft line can look like
    value that isn't really there (confirmed in practice: every "value bet"
    found this way turned out to have an unrealistic 25-97% EV). The median
    across many books is a much more robust stand-in for the true market
    price, and is how professional quants treat market consensus.
    """
    valid = [o for o in all_odds if o.home_odds > 1 and o.draw_odds > 1 and o.away_odds > 1]
    if not valid:
        return None

    return ConsensusOdds(
        bookmaker=f"Консенсус ({len(valid)} букмекеров)",
        home_odds=round(_median([o.home_odds for o in valid]), 3),
        draw_odds=round(_median([o.draw_odds for o in valid]), 3),
        away_odds=round(_median([o.away_odds for o in valid]), 3),
        n_bookmakers=len(valid),
    )
