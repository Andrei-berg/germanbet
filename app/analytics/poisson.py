from __future__ import annotations
from scipy.stats import poisson


def calculate_lambdas(
    home_goals_for: float,
    home_goals_against: float,
    away_goals_for: float,
    away_goals_against: float,
    avg_home_goals: float,
    avg_away_goals: float,
) -> Tuple[float, float]:
    if avg_home_goals <= 0 or avg_away_goals <= 0:
        return avg_home_goals, avg_away_goals

    home_attack = home_goals_for / avg_home_goals if avg_home_goals > 0 else 1.0
    home_defense = home_goals_against / avg_away_goals if avg_away_goals > 0 else 1.0
    away_attack = away_goals_for / avg_away_goals if avg_away_goals > 0 else 1.0
    away_defense = away_goals_against / avg_home_goals if avg_home_goals > 0 else 1.0

    lambda_home = home_attack * away_defense * avg_home_goals
    lambda_away = away_attack * home_defense * avg_away_goals

    return lambda_home, lambda_away


# Dixon-Coles (1997) low-score correlation adjustment: independent Poisson
# systematically under-predicts 0-0/1-1 draws and over-predicts 1-0/0-1, since
# real matches have a small negative correlation between the two teams'
# goal counts (rho) that plain independent Poisson can't represent. -0.1 is
# the literature-standard value for football (Dixon & Coles' own English
# football fit); it could be refit via MLE against our own historical
# results if more precision is ever needed, but a fixed literature value is
# a solid, well-tested default.
DIXON_COLES_RHO = -0.1


def _dixon_coles_tau(home_goals: int, away_goals: int, lambda_home: float, lambda_away: float, rho: float) -> float:
    if home_goals == 0 and away_goals == 0:
        return 1.0 - (lambda_home * lambda_away * rho)
    if home_goals == 0 and away_goals == 1:
        return 1.0 + (lambda_home * rho)
    if home_goals == 1 and away_goals == 0:
        return 1.0 + (lambda_away * rho)
    if home_goals == 1 and away_goals == 1:
        return 1.0 - rho
    return 1.0


def match_probabilities(
    lambda_home: float, lambda_away: float, max_goals: int = 10, rho: float = DIXON_COLES_RHO
) -> Tuple[float, float, float]:
    prob_home = 0.0
    prob_draw = 0.0
    prob_away = 0.0

    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = (
                poisson.pmf(i, lambda_home)
                * poisson.pmf(j, lambda_away)
                * _dixon_coles_tau(i, j, lambda_home, lambda_away, rho)
            )
            if i > j:
                prob_home += p
            elif i == j:
                prob_draw += p
            else:
                prob_away += p

    total = prob_home + prob_draw + prob_away
    return prob_home / total, prob_draw / total, prob_away / total
