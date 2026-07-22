def bookmaker_margin(
    home_odds: float, draw_odds: float, away_odds: float
) -> float:
    if home_odds <= 0 or draw_odds <= 0 or away_odds <= 0:
        return 0.0
    implied = (1.0 / home_odds) + (1.0 / draw_odds) + (1.0 / away_odds)
    return implied - 1.0


def remove_margin(
    home_odds: float, draw_odds: float, away_odds: float
) -> tuple[float, float, float]:
    total_implied = (1.0 / home_odds) + (1.0 / draw_odds) + (1.0 / away_odds)
    if total_implied <= 0:
        return home_odds, draw_odds, away_odds

    fair_home = total_implied * home_odds
    fair_draw = total_implied * draw_odds
    fair_away = total_implied * away_odds
    return fair_home, fair_draw, fair_away
