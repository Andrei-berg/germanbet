from __future__ import annotations
from app.models import Match


def recent_form(team_id: int, limit: int = 5) -> list[dict]:
    matches = (
        Match.query.filter(
            ((Match.home_team_id == team_id) | (Match.away_team_id == team_id)),
            Match.status == "finished",
        )
        .order_by(Match.match_date.desc())
        .limit(limit)
        .all()
    )

    results = []
    for m in matches:
        if m.home_goals is None:
            continue
        if m.home_team_id == team_id:
            if m.home_goals > m.away_goals:
                results.append({"result": "W", "gf": m.home_goals, "ga": m.away_goals})
            elif m.home_goals == m.away_goals:
                results.append({"result": "D", "gf": m.home_goals, "ga": m.away_goals})
            else:
                results.append({"result": "L", "gf": m.home_goals, "ga": m.away_goals})
        else:
            if m.away_goals > m.home_goals:
                results.append({"result": "W", "gf": m.away_goals, "ga": m.home_goals})
            elif m.away_goals == m.home_goals:
                results.append({"result": "D", "gf": m.away_goals, "ga": m.home_goals})
            else:
                results.append({"result": "L", "gf": m.away_goals, "ga": m.home_goals})

    return results


def form_weight(team_id: int, limit: int = 5) -> float:
    results = recent_form(team_id, limit)
    if not results:
        return 0.5

    weights = [1.0, 0.9, 0.8, 0.7, 0.6][: len(results)]
    score = 0.0
    for r, w in zip(results, weights):
        if r["result"] == "W":
            score += w
        elif r["result"] == "D":
            score += w * 0.4

    max_possible = sum(weights)
    return score / max_possible if max_possible > 0 else 0.5


def form_bonus(home_team_id: int, away_team_id: int) -> tuple[float, float]:
    hw = form_weight(home_team_id)
    aw = form_weight(away_team_id)
    total = hw + aw
    if total == 0:
        return 0.5, 0.5
    return hw / total, aw / total
