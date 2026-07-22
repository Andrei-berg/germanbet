from __future__ import annotations

HOME_ADVANTAGE = 100
K_FACTOR = 30
# Half-width (in elo points) of the "draw band" around the win/loss threshold.
# Calibrated so two evenly-matched teams (rating diff = 0) get ~27% draw
# probability, roughly matching historical football draw rates.
DRAW_MARGIN = 100


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))


def update_elo(
    home_elo: float,
    away_elo: float,
    home_goals: int,
    away_goals: int,
) -> tuple[float, float]:
    home_expected = expected_score(home_elo + HOME_ADVANTAGE, away_elo)
    away_expected = expected_score(away_elo, home_elo + HOME_ADVANTAGE)

    if home_goals > away_goals:
        home_score, away_score = 1.0, 0.0
    elif home_goals == away_goals:
        home_score, away_score = 0.5, 0.5
    else:
        home_score, away_score = 0.0, 1.0

    goal_diff = abs(home_goals - away_goals)
    margin_multiplier = 1.0
    if goal_diff == 2:
        margin_multiplier = 1.5
    elif goal_diff >= 3:
        margin_multiplier = 1.75 + (goal_diff - 3) * 0.125

    new_home_elo = home_elo + K_FACTOR * margin_multiplier * (home_score - home_expected)
    new_away_elo = away_elo + K_FACTOR * margin_multiplier * (away_score - away_expected)

    return round(new_home_elo, 1), round(new_away_elo, 1)


def elo_to_probability(home_elo: float, away_elo: float) -> tuple[float, float, float]:
    # NOTE: expected_score(a, b) + expected_score(b, a) == 1 always, so deriving
    # draw probability as 1 - home_exp - away_exp (the previous approach)
    # collapses to ~0 for every match. Instead, treat home/away win as crossing
    # a threshold offset by +/-DRAW_MARGIN from the raw rating diff, leaving a
    # band in between that becomes the draw probability.
    diff = home_elo + HOME_ADVANTAGE - away_elo
    home_prob = 1.0 / (1.0 + 10 ** (-(diff - DRAW_MARGIN) / 400))
    away_prob = 1.0 / (1.0 + 10 ** ((diff + DRAW_MARGIN) / 400))
    draw_prob = 1.0 - home_prob - away_prob
    return home_prob, draw_prob, away_prob
