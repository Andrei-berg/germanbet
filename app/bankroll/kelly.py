from __future__ import annotations

def kelly_criterion(odds: float, probability: float) -> float:
    if odds <= 1.0 or not (0 < probability < 1):
        return 0.0

    numerator = odds * probability - 1.0
    if numerator <= 0:
        return 0.0

    return numerator / (odds - 1.0)


def fractional_kelly(full_kelly: float, fraction: float = 0.25) -> float:
    return full_kelly * fraction


def apply_stop_loss(
    bet_pct: float, max_bet_pct: float = 0.03
) -> float:
    return min(bet_pct, max_bet_pct)


def calculate_bet(
    odds: float,
    probability: float,
    bankroll: float,
    kelly_fraction: float = 0.25,
    max_bet_pct: float = 0.03,
    min_ev: float = 0.02,
) -> dict:
    ev = odds * probability - 1.0

    if ev < min_ev:
        return {
            "verdict": "Пропустить: EV ниже порога",
            "ev": ev,
            "kelly_full_pct": 0.0,
            "bet_pct": 0.0,
            "bet_amount": 0.0,
        }

    full_kelly = kelly_criterion(odds, probability)
    if full_kelly <= 0:
        return {
            "verdict": "Пропустить: Келли ≤ 0",
            "ev": ev,
            "kelly_full_pct": 0.0,
            "bet_pct": 0.0,
            "bet_amount": 0.0,
        }

    bet_pct = fractional_kelly(full_kelly, kelly_fraction)
    bet_pct = apply_stop_loss(bet_pct, max_bet_pct)

    bet_amount = round(bankroll * bet_pct, 2)

    return {
        "verdict": "ВХОДИМ",
        "ev": round(ev * 100, 2),
        "kelly_full_pct": round(full_kelly * 100, 2),
        "bet_pct": round(bet_pct * 100, 2),
        "bet_amount": bet_amount,
    }
