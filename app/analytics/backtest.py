from __future__ import annotations

import math
from itertools import product
from dataclasses import dataclass
from app.models import Match, Odds
from app.analytics.poisson import calculate_lambdas, match_probabilities
from app.analytics.elo import elo_to_probability, update_elo
from app.bankroll.kelly import calculate_bet
from app.config import Config
from app.constants import LEAGUE_AVG_HOME, LEAGUE_AVG_AWAY
from app.aggregator.odds_selector import pick_reference_odds

BACKTEST_STAKE = 1000
FORM_WEIGHTS = [1.0, 0.9, 0.8, 0.7, 0.6]


@dataclass
class BacktestResult:
    poisson_weight: float
    elo_weight: float
    form_weight: float
    accuracy: float
    roi: float
    profit: float
    total_bets: int
    correct: int
    # Calibration, not just "did we pick the right winner": Brier score
    # (mean squared error of the full H/D/A probability vector against the
    # one-hot actual result, range 0-2, lower is better) and log-loss
    # (-log of the probability the model gave the actual outcome, lower is
    # better, 0 is a perfect confident-and-correct call). A model can have
    # good accuracy while being badly calibrated (e.g. always 90% confident
    # when it's really only right 60% of the time) - these catch that.
    brier_score: float
    log_loss: float


def _new_team_state() -> dict:
    return {
        "elo": 1500.0,
        "home_gf": 0.0, "home_ga": 0.0, "home_n": 0,
        "away_gf": 0.0, "away_ga": 0.0, "away_n": 0,
        "recent": [],  # most-recent-first results, strictly before the current match
    }


def _replay_form_weight(recent: list[str]) -> float:
    results = recent[:5]
    if not results:
        return 0.5

    weights = FORM_WEIGHTS[: len(results)]
    score = 0.0
    for r, w in zip(results, weights):
        if r == "W":
            score += w
        elif r == "D":
            score += w * 0.4

    max_possible = sum(weights)
    return score / max_possible if max_possible > 0 else 0.5


def _load_match_data() -> list[dict]:
    """Replay finished matches in chronological order, predicting each one only
    from Elo/goal-average/form state as it stood *before* that match.

    The previous version read `Team.elo_rating` and `Team.home_goals_for` etc.
    directly - but those are today's cumulative values, updated using every
    match played since, including ones that happened *after* the match being
    backtested. That leaks future results into "predictions" of the past and
    makes the reported accuracy/ROI (and the weights `set_optimal_weights`
    picks) meaningless. Replaying history and updating state only after each
    match is scored removes that leakage.
    """
    matches = (
        Match.query.filter(Match.status == "finished", Match.home_goals.isnot(None))
        .order_by(Match.match_date.asc(), Match.id.asc())
        .all()
    )

    states: dict[int, dict] = {}

    def state_for(team_id: int) -> dict:
        if team_id not in states:
            states[team_id] = _new_team_state()
        return states[team_id]

    data = []
    for m in matches:
        ht = state_for(m.home_team_id)
        at = state_for(m.away_team_id)

        home_avg_gf = ht["home_gf"] / ht["home_n"] if ht["home_n"] > 0 else LEAGUE_AVG_HOME
        home_avg_ga = ht["home_ga"] / ht["home_n"] if ht["home_n"] > 0 else LEAGUE_AVG_AWAY
        away_avg_gf = at["away_gf"] / at["away_n"] if at["away_n"] > 0 else LEAGUE_AVG_AWAY
        away_avg_ga = at["away_ga"] / at["away_n"] if at["away_n"] > 0 else LEAGUE_AVG_HOME

        lam_h, lam_a = calculate_lambdas(
            home_avg_gf, home_avg_ga, away_avg_gf, away_avg_ga,
            LEAGUE_AVG_HOME, LEAGUE_AVG_AWAY,
        )
        p_pois_h, p_pois_d, p_pois_a = match_probabilities(lam_h, lam_a)
        p_elo_h, p_elo_d, p_elo_a = elo_to_probability(ht["elo"], at["elo"])

        fh = _replay_form_weight(ht["recent"])
        fa = _replay_form_weight(at["recent"])
        form_total = fh + fa
        if form_total > 0:
            fh /= form_total
            fa /= form_total
        else:
            fh = fa = 0.5
        fd = 1.0 - fh - fa
        if fd < 0:
            fd = 0.0
            total = fh + fa
            fh /= total
            fa /= total

        ref_odds = pick_reference_odds(Odds.query.filter_by(match_id=m.id).all())
        odds_by_outcome = (
            {"H": ref_odds.home_odds, "D": ref_odds.draw_odds, "A": ref_odds.away_odds}
            if ref_odds else {"H": None, "D": None, "A": None}
        )

        actual = m.result()
        data.append({
            "actual": actual,
            "pois": (p_pois_h, p_pois_d, p_pois_a),
            "elo": (p_elo_h, p_elo_d, p_elo_a),
            "form": (fh, fd, fa),
            "odds": odds_by_outcome,
        })

        # Only now, after scoring this match, fold its real result into the
        # running state so it can inform later (chronologically) matches.
        new_home_elo, new_away_elo = update_elo(ht["elo"], at["elo"], m.home_goals, m.away_goals)
        ht["elo"], at["elo"] = new_home_elo, new_away_elo

        ht["home_gf"] += m.home_goals
        ht["home_ga"] += m.away_goals
        ht["home_n"] += 1
        at["away_gf"] += m.away_goals
        at["away_ga"] += m.home_goals
        at["away_n"] += 1

        if actual == "H":
            home_result, away_result = "W", "L"
        elif actual == "D":
            home_result, away_result = "D", "D"
        else:
            home_result, away_result = "L", "W"
        ht["recent"].insert(0, home_result)
        at["recent"].insert(0, away_result)

    return data


def evaluate_weights(
    pw: float, ew: float, fw: float, match_data: list[dict]
) -> BacktestResult:
    correct = 0
    total_profit = 0.0
    total_staked = 0.0
    total_bets = 0
    total_brier = 0.0
    total_logloss = 0.0

    for m in match_data:
        hp = pw * m["pois"][0] + ew * m["elo"][0] + fw * m["form"][0]
        dp = pw * m["pois"][1] + ew * m["elo"][1] + fw * m["form"][1]
        ap = pw * m["pois"][2] + ew * m["elo"][2] + fw * m["form"][2]
        total = hp + dp + ap
        hp /= total
        dp /= total
        ap /= total

        outcomes = [("H", hp), ("D", dp), ("A", ap)]
        predicted = max(outcomes, key=lambda x: x[1])

        if predicted[0] == m["actual"]:
            correct += 1

        probs_by_outcome = {"H": hp, "D": dp, "A": ap}
        for outcome, prob in probs_by_outcome.items():
            actual_indicator = 1.0 if outcome == m["actual"] else 0.0
            total_brier += (prob - actual_indicator) ** 2
        p_actual = max(probs_by_outcome[m["actual"]], 1e-10)
        total_logloss += -math.log(p_actual)

        odds = m["odds"][predicted[0]]
        if not odds or odds <= 1:
            continue

        ev = odds * predicted[1] - 1.0
        if ev >= 0.02:
            # Mirrors value_evaluator.calculate_bet()'s actual staking rules
            # (fractional Kelly + a hard cap on bankroll %) - using raw,
            # uncapped full Kelly here (as an earlier version did) let a
            # single overconfident prediction (e.g. from an over-weighted
            # form signal) size a bet at 90%+ of bankroll, which produced
            # backtest "ROI" in the thousands of percent that could never
            # happen under the app's real betting rules.
            bet = calculate_bet(
                odds=odds,
                probability=predicted[1],
                bankroll=BACKTEST_STAKE,
                kelly_fraction=Config.KELLY_FRACTION,
                max_bet_pct=Config.MAX_BET_PCT,
                min_ev=0.02,
            )
            stake = bet["bet_amount"]
            if stake > 0:
                total_bets += 1
                total_staked += stake
                if m["actual"] == predicted[0]:
                    total_profit += stake * (odds - 1.0)
                else:
                    total_profit -= stake

    n = len(match_data)
    acc = correct / n * 100 if n else 0.0
    # Return on money actually risked, not on bet *count* - dividing by
    # total_bets instead of total_staked silently treated "average profit
    # per bet" as if $1 had been staked each time, inflating ROI by roughly
    # the real average stake size (previously reported 1000%+ "returns").
    roi = total_profit / total_staked * 100 if total_staked > 0 else 0.0
    brier = total_brier / n if n else 0.0
    logloss = total_logloss / n if n else 0.0

    return BacktestResult(
        poisson_weight=round(pw, 2),
        elo_weight=round(ew, 2),
        form_weight=round(fw, 2),
        accuracy=round(acc, 1),
        roi=round(roi, 2),
        profit=round(total_profit, 2),
        total_bets=total_bets,
        correct=correct,
        brier_score=round(brier, 4),
        log_loss=round(logloss, 4),
    )


def run_backtest(step: float = 0.1) -> list[BacktestResult]:
    match_data = _load_match_data()
    if not match_data:
        return []

    all_vals = [round(i * step, 1) for i in range(int(1.0 / step) + 1)]
    results: list[BacktestResult] = []

    for pw, ew in product(all_vals, all_vals):
        fw = round(1.0 - pw - ew, 1)
        if fw < -0.01 or fw > 1.01:
            continue
        fw = max(0.0, min(1.0, fw))
        result = evaluate_weights(pw, ew, fw, match_data)
        results.append(result)

    results.sort(key=lambda r: r.roi, reverse=True)
    return results


def find_best_weights(metric: str = "roi", top_n: int = 5) -> list[BacktestResult]:
    results = run_backtest(step=0.1)
    if metric == "accuracy":
        results.sort(key=lambda r: r.accuracy, reverse=True)
    elif metric == "profit":
        results.sort(key=lambda r: r.profit, reverse=True)
    elif metric in ("brier", "brier_score"):
        results.sort(key=lambda r: r.brier_score)  # lower is better
    elif metric in ("logloss", "log_loss"):
        results.sort(key=lambda r: r.log_loss)  # lower is better
    elif metric == "balanced":
        # The pure ROI-optimum reliably turns out to be the worst-calibrated
        # combo (it's exploiting overconfident probabilities that happen to
        # have paid off in this sample, not a real edge) - confirmed here by
        # comparing brier_score across combos. Rank-sum across ROI and Brier
        # picks a combo that's good on both, rather than best on either
        # extreme.
        roi_rank = {id(r): i for i, r in enumerate(sorted(results, key=lambda r: r.roi, reverse=True))}
        brier_rank = {id(r): i for i, r in enumerate(sorted(results, key=lambda r: r.brier_score))}
        results.sort(key=lambda r: roi_rank[id(r)] + brier_rank[id(r)])
    return results[:top_n]


def set_optimal_weights() -> dict:
    best = find_best_weights(metric="roi", top_n=1)
    if not best:
        return {"error": "no finished matches for backtest"}

    b = best[0]
    return {
        "poisson_weight": b.poisson_weight,
        "elo_weight": b.elo_weight,
        "form_weight": b.form_weight,
        "accuracy": b.accuracy,
        "roi": b.roi,
    }
