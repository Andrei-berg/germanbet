from app.analytics.poisson import calculate_lambdas, match_probabilities


def test_calculate_lambdas():
    lam_h, lam_a = calculate_lambdas(
        home_goals_for=2.0,
        home_goals_against=0.8,
        away_goals_for=1.2,
        away_goals_against=1.5,
        avg_home_goals=1.53,
        avg_away_goals=1.15,
    )
    assert lam_h > 0
    assert lam_a > 0


def test_match_probabilities():
    p_h, p_d, p_a = match_probabilities(1.8, 1.2)
    total = p_h + p_d + p_a
    assert abs(total - 1.0) < 0.01
    assert p_h > p_a  # stronger home team


def test_strong_favorite():
    p_h, p_d, p_a = match_probabilities(3.0, 0.5)
    assert p_h > 0.8
    assert p_a < 0.1
    assert abs(p_h + p_d + p_a - 1.0) < 0.01
