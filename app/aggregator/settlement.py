from __future__ import annotations
from app import db
from app.models import Match, Bet
from app.analytics.elo import update_elo


def finish_match(match: Match, home_goals: int, away_goals: int) -> None:
    """Record a real result and roll it into everything that depends on it:
    Elo ratings, per-team goal averages, and any pending bets on the match.
    This is the only place that should mark a match finished with a real
    score - callers must never fabricate a result.
    """
    match.status = "finished"
    match.home_goals = home_goals
    match.away_goals = away_goals
    db.session.flush()

    home_team = match.home_team
    away_team = match.away_team

    new_home_elo, new_away_elo = update_elo(
        home_team.elo_rating, away_team.elo_rating, home_goals, away_goals
    )
    home_team.elo_rating = new_home_elo
    away_team.elo_rating = new_away_elo

    home_team.update_stats()
    away_team.update_stats()
    db.session.flush()

    _auto_settle_bets(match)


def _auto_settle_bets(match: Match) -> None:
    from app.bankroll.bankroll_manager import update_bank

    bets = Bet.query.filter_by(match_id=match.id, result="pending").all()
    if not bets:
        return

    actual = match.result()
    if actual is None:
        return

    for bet in bets:
        if bet.outcome == actual:
            bet.result = "W"
            bet.profit = round(bet.stake * (bet.odds - 1.0), 2)
            update_bank(
                bet.stake + bet.profit,
                f"Auto-settle: Win #{bet.id} ({match.home_team.name} vs {match.away_team.name})",
            )
        else:
            bet.result = "L"
            bet.profit = -bet.stake
            update_bank(
                0,
                f"Auto-settle: Loss #{bet.id} ({match.home_team.name} vs {match.away_team.name})",
            )
