from __future__ import annotations
from app import db
from app.models import Match, Odds, Prediction, Bet
from app.config import Config
from app.settings_helper import load_settings
from app.analytics.true_probability import compute_probabilities
from app.value_finder.margin_calc import remove_margin
from app.bankroll.kelly import calculate_bet
from app.bankroll.bankroll_manager import get_current_bank
from app.aggregator.odds_selector import pick_reference_odds
from app.constants import MIN_MATCHES_FOR_BET, MAX_TRUSTED_EV_PCT
from datetime import datetime, timezone


def _get_model_weights():
    s = load_settings()
    return {
        "poisson_weight": s.get("poisson_weight", 0.50),
        "elo_weight": s.get("elo_weight", 0.30),
        "form_weight": s.get("form_weight", 0.20),
        "default_bank": s.get("default_bank", Config.DEFAULT_BANK),
        "kelly_fraction": s.get("kelly_fraction", Config.KELLY_FRACTION),
        "max_bet_pct": s.get("max_bet_pct", Config.MAX_BET_PCT),
        "min_ev": s.get("min_ev", Config.MIN_EV),
    }


def evaluate_match(match_id: int) -> Prediction | None:
    match = Match.query.get(match_id)
    if not match:
        return None

    weights = _get_model_weights()

    home_prob, draw_prob, away_prob = compute_probabilities(
        match.home_team, match.away_team,
        poisson_weight=weights["poisson_weight"],
        elo_weight=weights["elo_weight"],
        form_weight=weights["form_weight"],
    )

    all_odds = Odds.query.filter_by(match_id=match_id).all()
    ref_odds = pick_reference_odds(all_odds)
    if ref_odds:
        best_odds_home = ref_odds.home_odds
        best_odds_draw = ref_odds.draw_odds
        best_odds_away = ref_odds.away_odds
    else:
        best_odds_home = best_odds_draw = best_odds_away = None

    prediction = Prediction(match_id=match_id, home_prob=home_prob, draw_prob=draw_prob, away_prob=away_prob)

    prediction.home_fair_odds = round(1.0 / home_prob, 4) if home_prob > 0 else 0
    prediction.draw_fair_odds = round(1.0 / draw_prob, 4) if draw_prob > 0 else 0
    prediction.away_fair_odds = round(1.0 / away_prob, 4) if away_prob > 0 else 0

    if best_odds_home:
        fair_h, fair_d, fair_a = remove_margin(
            best_odds_home, best_odds_draw, best_odds_away
        )

        margin_pct = round((1.0 / best_odds_home + 1.0 / best_odds_draw + 1.0 / best_odds_away - 1.0) * 100, 2)

        prediction.ev_home = round((best_odds_home * home_prob - 1.0) * 100, 2)
        prediction.ev_draw = round((best_odds_draw * draw_prob - 1.0) * 100, 2)
        prediction.ev_away = round((best_odds_away * away_prob - 1.0) * 100, 2)

        ev_fair_home = round((fair_h * home_prob - 1.0) * 100, 2)
        ev_fair_draw = round((fair_d * draw_prob - 1.0) * 100, 2)
        ev_fair_away = round((fair_a * away_prob - 1.0) * 100, 2)

        outcomes = [
            ("H", best_odds_home, home_prob, prediction.ev_home, ev_fair_home),
            ("D", best_odds_draw, draw_prob, prediction.ev_draw, ev_fair_draw),
            ("A", best_odds_away, away_prob, prediction.ev_away, ev_fair_away),
        ]

        best_outcome = max(outcomes, key=lambda x: x[3])
        prediction.ev = best_outcome[3]

        has_edge_over_fair = best_outcome[4] > 0
        min_ev = weights.get("min_ev", Config.MIN_EV)

        has_enough_history = (
            match.home_team.matches_played >= MIN_MATCHES_FOR_BET
            and match.away_team.matches_played >= MIN_MATCHES_FOR_BET
        )

        if not has_enough_history:
            prediction.verdict = (
                f"Пропустить: мало истории "
                f"({match.home_team.matches_played}/{match.away_team.matches_played} матчей)"
            )
        elif prediction.ev > min_ev * 100 and has_edge_over_fair:
            bank = get_current_bank()
            if bank <= 0:
                bank = weights.get("default_bank", Config.DEFAULT_BANK)

            bet_result = calculate_bet(
                odds=best_outcome[1],
                probability=best_outcome[2],
                bankroll=bank,
                kelly_fraction=weights.get("kelly_fraction", Config.KELLY_FRACTION),
                max_bet_pct=weights.get("max_bet_pct", Config.MAX_BET_PCT),
                min_ev=min_ev,
            )

            prediction.kelly_pct = bet_result["kelly_full_pct"]
            prediction.bet_pct = bet_result["bet_pct"]
            prediction.bet_amount = bet_result["bet_amount"]

            if prediction.ev > MAX_TRUSTED_EV_PCT:
                # An EV this high against a real market is far more likely a
                # model error (thin sample, missing injury/lineup info, a
                # stale single-bookmaker price) than genuine value - keep the
                # numbers visible for reference but don't badge it as a
                # normal recommended bet, and keep it out of bulk-bet/
                # value_bets counts (both key off "ВХОДИМ" in the verdict).
                prediction.verdict = (
                    f"{best_outcome[0]} | ПОДОЗРИТЕЛЬНО: EV {prediction.ev}% "
                    f"выше {MAX_TRUSTED_EV_PCT:.0f}% - проверьте вручную перед ставкой"
                )
            else:
                prediction.verdict = f"{best_outcome[0]} | {bet_result['verdict']}"
        else:
            prediction.verdict = "Пропустить"
    else:
        prediction.verdict = "Нет коэффициентов"

    prediction.created_at = datetime.now(timezone.utc)

    Prediction.query.filter_by(match_id=match_id).delete()
    db.session.flush()
    db.session.expire_all()
    db.session.add(prediction)
    db.session.commit()

    return prediction


def evaluate_all_upcoming() -> int:
    matches = Match.query.filter(
        Match.status == "scheduled",
        Match.match_date >= datetime.now(timezone.utc),
    ).all()

    count = 0
    for m in matches:
        if Odds.query.filter_by(match_id=m.id).first():
            evaluate_match(m.id)
            count += 1

    return count
