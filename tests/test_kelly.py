from app.bankroll.kelly import kelly_criterion, fractional_kelly, apply_stop_loss, calculate_bet


def test_kelly_positive_ev():
    f = kelly_criterion(odds=2.50, probability=0.45)
    expected = (2.5 * 0.45 - 1.0) / (2.5 - 1.0)
    assert abs(f - expected) < 0.001
    assert f > 0


def test_kelly_negative_ev():
    f = kelly_criterion(odds=1.50, probability=0.60)
    assert f == 0.0


def test_fractional():
    assert fractional_kelly(0.20, 0.25) == 0.05


def test_stop_loss():
    assert apply_stop_loss(0.10, 0.03) == 0.03
    assert apply_stop_loss(0.02, 0.03) == 0.02


def test_calculate_bet_value():
    result = calculate_bet(2.50, 0.45, bankroll=100000)
    assert result["verdict"] == "ВХОДИМ"
    assert result["bet_amount"] > 0
    assert 0 < result["bet_pct"] <= 3.0


def test_calculate_bet_low_ev():
    result = calculate_bet(1.50, 0.60, bankroll=100000, min_ev=0.02)
    assert "Пропустить" in result["verdict"]


def test_calculate_bet_edge():
    result = calculate_bet(4.00, 0.30, bankroll=100000, kelly_fraction=1.0, max_bet_pct=0.5)
    assert result["verdict"] == "ВХОДИМ"
    assert result["bet_pct"] <= 50.0
