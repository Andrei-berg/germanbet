from app.analytics.elo import elo_to_probability


def test_draw_probability_is_not_zero_for_even_teams():
    home_prob, draw_prob, away_prob = elo_to_probability(1600, 1600)
    assert draw_prob > 0.15
    assert abs(home_prob + draw_prob + away_prob - 1.0) < 1e-9


def test_draw_probability_shrinks_for_big_favorite():
    _, draw_even, _ = elo_to_probability(1600, 1600)
    _, draw_mismatch, _ = elo_to_probability(1900, 1500)
    assert draw_mismatch < draw_even


def test_home_advantage_favors_home_team_between_equal_ratings():
    home_prob, _, away_prob = elo_to_probability(1600, 1600)
    assert home_prob > away_prob
