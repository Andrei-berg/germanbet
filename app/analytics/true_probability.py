from __future__ import annotations
from app.analytics.poisson import calculate_lambdas, match_probabilities
from app.analytics.elo import elo_to_probability
from app.analytics.form_analyzer import form_bonus
from app.constants import LEAGUE_AVG_HOME, LEAGUE_AVG_AWAY


def compute_probabilities(
    home_team,
    away_team,
    poisson_weight: float = 0.50,
    elo_weight: float = 0.30,
    form_weight: float = 0.20,
) -> tuple[float, float, float]:
    home_avg_gf = home_team.home_goals_for if home_team.home_goals_for > 0 else LEAGUE_AVG_HOME
    home_avg_ga = home_team.home_goals_against if home_team.home_goals_against > 0 else LEAGUE_AVG_AWAY
    away_avg_gf = away_team.away_goals_for if away_team.away_goals_for > 0 else LEAGUE_AVG_AWAY
    away_avg_ga = away_team.away_goals_against if away_team.away_goals_against > 0 else LEAGUE_AVG_HOME

    lam_h, lam_a = calculate_lambdas(
        home_avg_gf,
        home_avg_ga,
        away_avg_gf,
        away_avg_ga,
        LEAGUE_AVG_HOME,
        LEAGUE_AVG_AWAY,
    )

    p_pois = match_probabilities(lam_h, lam_a)
    p_elo = elo_to_probability(home_team.elo_rating, away_team.elo_rating)

    form_h, form_a = form_bonus(home_team.id, away_team.id)
    form_draw = 1.0 - form_h - form_a
    if form_draw < 0:
        form_draw = 0.0
        total = form_h + form_a
        form_h /= total
        form_a /= total

    home_prob = (
        poisson_weight * p_pois[0]
        + elo_weight * p_elo[0]
        + form_weight * form_h
    )
    draw_prob = (
        poisson_weight * p_pois[1]
        + elo_weight * p_elo[1]
        + form_weight * form_draw
    )
    away_prob = (
        poisson_weight * p_pois[2]
        + elo_weight * p_elo[2]
        + form_weight * form_a
    )

    total = home_prob + draw_prob + away_prob
    return home_prob / total, draw_prob / total, away_prob / total
