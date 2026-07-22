from datetime import datetime, timezone, timedelta
import csv
import io
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, Response, flash
from app import db
from app.models import Match, Odds, Prediction, Bet, BankrollLog, Setting
from app.config import Config
from app.settings_helper import load_settings, save_setting
from app.value_finder.value_evaluator import evaluate_match, evaluate_all_upcoming
from app.aggregator.thesportsdb_client import sync_finished_matches
from app.bankroll.bankroll_manager import get_current_bank, set_initial_bank, update_bank
from app.aggregator.odds_selector import pick_reference_odds

bp = Blueprint("main", __name__)


@bp.route("/")
def dashboard():
    bank = get_current_bank()
    if bank <= 0:
        set_initial_bank(Config.DEFAULT_BANK)
        bank = Config.DEFAULT_BANK

    leagues = [r[0] for r in Match.query.with_entities(Match.league).distinct().order_by(Match.league).all()]
    selected_league = request.args.get("league", "")

    now = datetime.now(timezone.utc)
    query = Match.query.filter(
        Match.status == "scheduled",
        Match.match_date >= now - timedelta(hours=2),
    )
    if selected_league:
        query = query.filter(Match.league == selected_league)
    matches = query.order_by(Match.match_date.asc()).all()

    match_data = []
    for m in matches:
        pred = Prediction.query.filter_by(match_id=m.id).order_by(Prediction.id.desc()).first()
        all_odds = Odds.query.filter_by(match_id=m.id).all()
        ref_odds = pick_reference_odds(all_odds)
        if ref_odds:
            display_odds = ref_odds
            best_source = {'home': ref_odds.bookmaker, 'draw': ref_odds.bookmaker, 'away': ref_odds.bookmaker}
        else:
            display_odds = None
            best_source = {}
        match_bets = Bet.query.filter_by(match_id=m.id).all()
        match_data.append({
            "match": m,
            "prediction": pred,
            "odds": display_odds,
            "all_odds": all_odds,
            "best_source": best_source,
            "bets": match_bets,
            "has_bet": len(match_bets) > 0,
        })

    active_bets = (
        Bet.query.filter_by(result="pending")
        .order_by(Bet.created_at.desc())
        .all()
    )

    recent_finished = Match.query.filter(
        Match.status == "finished",
        Match.home_goals.isnot(None),
    ).order_by(Match.match_date.desc()).limit(5).all()

    recent_results = []
    for m in recent_finished:
        match_bets = Bet.query.filter_by(match_id=m.id).all()
        recent_results.append({
            "match": m,
            "bets": match_bets,
        })

    stats = {
        "bank": bank,
        "upcoming": Match.query.filter(Match.status == "scheduled").count(),
        "value_bets": Prediction.query.filter(
            Prediction.verdict.like("%ВХОДИМ%")
        ).count(),
        "total_bets": Bet.query.count(),
        "pending": len(active_bets),
    }

    return render_template(
        "dashboard.html",
        match_data=match_data,
        active_bets=active_bets,
        recent_results=recent_results,
        stats=stats,
        now=now,
        leagues=leagues,
        selected_league=selected_league,
    )


@bp.route("/match/<int:match_id>")
def match_detail(match_id):
    match = Match.query.get_or_404(match_id)
    pred = Prediction.query.filter_by(match_id=match_id).order_by(Prediction.id.desc()).first()
    odds_list = Odds.query.filter_by(match_id=match_id).order_by(Odds.timestamp.desc()).all()

    if not pred:
        pred = evaluate_match(match_id)

    ref_odds = pick_reference_odds(odds_list)
    if ref_odds:
        best_home = ref_odds.home_odds
        best_draw = ref_odds.draw_odds
        best_away = ref_odds.away_odds
        best_source = {'home': ref_odds.bookmaker, 'draw': ref_odds.bookmaker, 'away': ref_odds.bookmaker}
    else:
        best_home = best_draw = best_away = None
        best_source = {}

    match_bets = Bet.query.filter_by(match_id=match_id).all()

    return render_template(
        "match_detail.html",
        match=match,
        prediction=pred,
        odds_list=odds_list,
        best_home=best_home,
        best_draw=best_draw,
        best_away=best_away,
        best_source=best_source,
        match_bets=match_bets,
    )


@bp.route("/match/<int:match_id>/refresh", methods=["POST"])
def refresh_match(match_id):
    evaluate_match(match_id)
    return redirect(url_for("main.match_detail", match_id=match_id))


def _best_odds_for_outcome(match_id: int, outcome: str) -> float | None:
    all_odds = Odds.query.filter_by(match_id=match_id).all()
    ref_odds = pick_reference_odds(all_odds)
    if not ref_odds:
        return None
    return {"H": ref_odds.home_odds, "D": ref_odds.draw_odds, "A": ref_odds.away_odds}.get(outcome)


@bp.route("/match/<int:match_id>/bet", methods=["POST"])
def place_bet(match_id):
    match = Match.query.get_or_404(match_id)
    outcome = request.form.get("outcome")
    odds = float(request.form.get("odds", 0))
    stake = float(request.form.get("stake", 0))

    if not outcome or odds <= 0 or stake <= 0:
        return redirect(url_for("main.match_detail", match_id=match_id))

    bet = Bet(match_id=match_id, outcome=outcome, odds=odds, stake=stake)
    db.session.add(bet)
    update_bank(-stake, f"Bet: {match.home_team.name} vs {match.away_team.name} - {outcome}")
    db.session.commit()

    flash(f"Ставка {outcome} @ {odds} на {match.home_team.name} vs {match.away_team.name} — {stake:,.0f} ₽", "success")
    return redirect(url_for("main.dashboard"))


@bp.route("/bulk-bet", methods=["POST"])
def bulk_bet():
    match_ids = request.form.getlist("match_ids")
    stake_mode = request.form.get("stake_mode", "kelly")

    if not match_ids:
        flash("Не выбрано ни одной ставки", "warning")
        return redirect(url_for("main.dashboard"))

    bank = get_current_bank()
    total_stake = 0.0
    placed = 0
    skipped = 0

    for mid in match_ids:
        try:
            match_id = int(mid)
        except (ValueError, TypeError):
            skipped += 1
            continue

        match = Match.query.get(match_id)
        if not match:
            skipped += 1
            continue

        if Bet.query.filter_by(match_id=match_id, result="pending").first():
            skipped += 1
            continue

        pred = Prediction.query.filter_by(match_id=match_id).order_by(Prediction.id.desc()).first()
        if not pred or not pred.verdict or "ВХОДИМ" not in pred.verdict:
            skipped += 1
            continue

        outcome = pred.verdict.split("|")[0].strip()
        if outcome not in ("H", "D", "A"):
            skipped += 1
            continue

        odds = _best_odds_for_outcome(match_id, outcome)
        if not odds or odds <= 0:
            skipped += 1
            continue

        if stake_mode == "kelly" and pred.bet_amount and pred.bet_amount > 0:
            stake = pred.bet_amount
        elif stake_mode == "fixed":
            try:
                stake = float(request.form.get("fixed_stake", 0))
            except (ValueError, TypeError):
                stake = 0
            if stake <= 0:
                stake = pred.bet_amount if pred.bet_amount and pred.bet_amount > 0 else 1000
        else:
            stake = pred.bet_amount if pred.bet_amount and pred.bet_amount > 0 else 1000

        if bank < total_stake + stake:
            flash(f"Недостаточно средств на банке для ставки #{match_id}", "danger")
            skipped += 1
            continue

        bet = Bet(match_id=match_id, outcome=outcome, odds=odds, stake=stake)
        db.session.add(bet)
        total_stake += stake
        placed += 1

    if placed:
        update_bank(-total_stake, f"Bulk bet: {placed} ставок")
        db.session.commit()
        flash(f"✅ Размещено {placed} ставок на сумму {total_stake:,.0f} ₽", "success")
    if skipped:
        flash(f"⏭ Пропущено {skipped} (нет прогноза/уже есть ставка/ошибка)", "info")

    return redirect(url_for("main.dashboard"))


@bp.route("/portfolio")
def portfolio():
    bets = Bet.query.order_by(Bet.created_at.desc()).all()
    bank_log = BankrollLog.query.order_by(BankrollLog.created_at.desc()).all()

    total_staked = sum(b.stake for b in bets if b.result in ("pending", "W", "L"))
    total_profit = sum(b.profit or 0 for b in bets if b.result in ("W", "L"))
    won = sum(1 for b in bets if b.result == "W")
    lost = sum(1 for b in bets if b.result == "L")
    pending = sum(1 for b in bets if b.result == "pending")
    win_rate = round(won / (won + lost) * 100, 1) if (won + lost) > 0 else 0
    roi = round(total_profit / total_staked * 100, 2) if total_staked > 0 else 0

    return render_template(
        "portfolio.html",
        bets=bets,
        bank_log=bank_log,
        stats={
            "total_staked": total_staked,
            "total_profit": total_profit,
            "won": won,
            "lost": lost,
            "pending": pending,
            "win_rate": win_rate,
            "roi": roi,
        },
    )


@bp.route("/bet/<int:bet_id>/settle", methods=["POST"])
def settle_bet(bet_id):
    bet = Bet.query.get_or_404(bet_id)
    result = request.form.get("result")

    if result in ("W", "L"):
        if result == "W":
            profit = round(bet.stake * (bet.odds - 1.0), 2)
        else:
            profit = -bet.stake

        bet.result = result
        bet.profit = profit
        update_bank(profit + bet.stake, f"Settlement: {'Win' if result == 'W' else 'Loss'} #{bet.id}")
        db.session.commit()

    return redirect(url_for("main.portfolio"))


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        bank = float(request.form.get("bank", str(Config.DEFAULT_BANK)))
        set_initial_bank(bank)

        save_setting("kelly_fraction", float(request.form.get("kelly_fraction", Config.KELLY_FRACTION)))
        save_setting("max_bet_pct", float(request.form.get("max_bet_pct", Config.MAX_BET_PCT)))
        save_setting("min_ev", float(request.form.get("min_ev", Config.MIN_EV)))
        save_setting("poisson_weight", float(request.form.get("poisson_weight", 0.50)))
        save_setting("elo_weight", float(request.form.get("elo_weight", 0.30)))
        save_setting("form_weight", float(request.form.get("form_weight", 0.20)))

        bank_after = get_current_bank()
        flash(f"Банк сохранён: {bank_after:,.0f} ₽", "success")
        return redirect(url_for("main.settings"))

    bank = get_current_bank()
    s = load_settings()
    return render_template("settings.html", bank=bank, config=Config, settings=s)


@bp.route("/results")
def results():
    finished = Match.query.filter(
        Match.status == "finished",
        Match.home_goals.isnot(None),
    ).order_by(Match.match_date.desc()).all()

    total_predictions = 0
    correct_predictions = 0
    results_list = []

    for m in finished:
        pred = Prediction.query.filter_by(match_id=m.id).order_by(Prediction.id.desc()).first()
        bets = Bet.query.filter_by(match_id=m.id).all()
        actual = m.result()

        pred_correct = None
        if pred and actual:
            pred_outcome = max(
                [("H", pred.home_prob), ("D", pred.draw_prob), ("A", pred.away_prob)],
                key=lambda x: x[1]
            )[0]
            pred_correct = pred_outcome == actual
            total_predictions += 1
            if pred_correct:
                correct_predictions += 1

        results_list.append({
            "match": m,
            "prediction": pred,
            "bets": bets,
            "actual": actual,
            "pred_correct": pred_correct,
        })

    accuracy = round(correct_predictions / total_predictions * 100, 1) if total_predictions > 0 else 0

    return render_template(
        "results.html",
        results=results_list,
        stats={
            "total": len(finished),
            "total_predictions": total_predictions,
            "correct_predictions": correct_predictions,
            "accuracy": accuracy,
        },
    )


@bp.route("/results/sync", methods=["POST"])
def sync_results():
    synced = sync_finished_matches()
    if synced:
        flash(f"Синхронизировано завершённых матчей: {synced}", "success")
    else:
        flash("Нет новых завершённых матчей", "info")
    return redirect(url_for("main.results"))


@bp.route("/update-all", methods=["POST"])
def update_all():
    count = evaluate_all_upcoming()
    return redirect(url_for("main.dashboard"))


@bp.route("/backtest", methods=["GET", "POST"])
def backtest():
    from app.analytics.backtest import run_backtest, set_optimal_weights
    from app.models import Match

    if request.method == "POST":
        if request.form.get("action") == "apply":
            result = set_optimal_weights()
            if "error" not in result:
                save_setting("poisson_weight", result["poisson_weight"])
                save_setting("elo_weight", result["elo_weight"])
                save_setting("form_weight", result["form_weight"])
            return redirect(url_for("main.backtest"))

    results = run_backtest(step=0.1)
    finished_count = Match.query.filter(Match.status == "finished", Match.home_goals.isnot(None)).count()
    return render_template("backtest.html", results=results[:10], total=len(results), finished_matches=finished_count)


@bp.route("/portfolio/export")
def portfolio_export():
    bets = Bet.query.order_by(Bet.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "date", "match", "outcome", "odds", "stake", "result", "profit"])

    for b in bets:
        match_name = ""
        if b.match:
            match_name = f"{b.match.home_team.name} vs {b.match.away_team.name}"

        writer.writerow([
            b.id,
            b.created_at.strftime("%Y-%m-%d %H:%M"),
            match_name,
            b.outcome,
            b.odds,
            b.stake,
            b.result,
            b.profit or 0,
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=portfolio.csv"},
    )


@bp.route("/api/match/<int:match_id>")
def api_match(match_id):
    match = Match.query.get_or_404(match_id)
    pred = Prediction.query.filter_by(match_id=match_id).order_by(Prediction.id.desc()).first()
    odds = Odds.query.filter_by(match_id=match_id).order_by(Odds.id.desc()).first()

    return jsonify({
        "match": {
            "id": match.id,
            "home": match.home_team.name,
            "away": match.away_team.name,
            "date": match.match_date.isoformat(),
        },
        "prediction": {
            "home_prob": round(pred.home_prob, 4) if pred else None,
            "draw_prob": round(pred.draw_prob, 4) if pred else None,
            "away_prob": round(pred.away_prob, 4) if pred else None,
            "ev": pred.ev if pred else None,
            "verdict": pred.verdict if pred else None,
        } if pred else None,
        "odds": {
            "home": odds.home_odds if odds else None,
            "draw": odds.draw_odds if odds else None,
            "away": odds.away_odds if odds else None,
            "bookmaker": odds.bookmaker if odds else None,
        } if odds else None,
    })
