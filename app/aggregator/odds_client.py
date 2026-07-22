from __future__ import annotations

import time
import requests
from datetime import datetime, timezone
from app import db
from app.models import Team, Match, Odds
from app.config import Config
from app.aggregator.settlement import finish_match
from app.aggregator.historical_backfill import normalize_team_name, canonical_team_name


BASE_URL = "https://api.the-odds-api.com/v4"

SPORTS_MAP = {
    "soccer_epl": "Premier League",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_spain_la_liga": "La Liga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_russia_premier_league": "Russian Premier League",
}


def _api_enabled() -> bool:
    return bool(Config.ODDS_API_KEY) and Config.ODDS_API_KEY != "test"


def fetch_odds_for_sport(
    sport: str = "soccer_epl",
    regions: str = "eu,uk",
    markets: str = "h2h",
) -> list[dict]:
    if not _api_enabled():
        return []

    try:
        r = requests.get(
            f"{BASE_URL}/sports/{sport}/odds",
            params={
                "apiKey": Config.ODDS_API_KEY,
                "regions": regions,
                "markets": markets,
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException:
        return []


def fetch_scores_for_sport(sport: str = "soccer_epl", days_from: int = 3) -> list[dict]:
    if not _api_enabled():
        return []

    try:
        r = requests.get(
            f"{BASE_URL}/sports/{sport}/scores",
            params={"apiKey": Config.ODDS_API_KEY, "daysFrom": days_from},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException:
        return []


def _get_or_create_team(name: str, league: str) -> Team:
    name = canonical_team_name(name)
    team = Team.query.filter_by(name=name).first()
    if team:
        return team
    team = Team(name=name, league=league)
    db.session.add(team)
    db.session.flush()
    return team


def sync_odds_from_api() -> int:
    if not _api_enabled():
        return 0

    total = 0
    for sport_key, league_name in SPORTS_MAP.items():
        data = fetch_odds_for_sport(sport_key)
        for event in data:
            home_name = event.get("home_team", "")
            away_name = event.get("away_team", "")
            if not home_name or not away_name:
                continue

            home_team = _get_or_create_team(home_name, league_name)
            away_team = _get_or_create_team(away_name, league_name)

            external_id = event.get("id")
            match = (
                Match.query.filter_by(external_id=external_id).first()
                if external_id else None
            )
            if not match:
                match = Match.query.filter_by(
                    home_team_id=home_team.id, away_team_id=away_team.id, status="scheduled"
                ).first()
            if not match:
                match = Match(
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                    league=league_name,
                    match_date=datetime.fromisoformat(
                        event.get("commence_time", "").replace("Z", "+00:00")
                    ),
                    status="scheduled",
                    external_id=external_id,
                )
                db.session.add(match)
                db.session.flush()

            for bm in event.get("bookmakers", []):
                bookmaker_name = bm.get("title", "Unknown")

                for market in bm.get("markets", []):
                    if market.get("key") != "h2h":
                        continue

                    outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                    if not outcomes:
                        continue

                    if home_name not in outcomes or away_name not in outcomes:
                        continue

                    odds = Odds(
                        match_id=match.id,
                        bookmaker=bookmaker_name,
                        home_odds=outcomes[home_name],
                        draw_odds=outcomes.get("Draw", 1.0),
                        away_odds=outcomes[away_name],
                    )
                    db.session.add(odds)
                    total += 1

    if total:
        db.session.commit()

    return total


def sync_results_from_odds_api() -> int:
    """Fetch real final scores for matches that were created from this
    provider (matched by the odds-api event id stored as Match.external_id)
    and settle them via `finish_match`, so Elo/goal-stats/pending bets all
    update from an actual result - never a fabricated one.
    """
    if not _api_enabled():
        return 0

    count = 0
    for sport_key in SPORTS_MAP:
        events = fetch_scores_for_sport(sport_key)
        for ev in events:
            if not ev.get("completed"):
                continue

            scores = ev.get("scores")
            if not scores:
                continue

            match = Match.query.filter_by(external_id=ev.get("id")).first()
            if not match or match.status != "scheduled":
                continue

            score_by_name = {s.get("name"): s.get("score") for s in scores}
            home_score = score_by_name.get(match.home_team.name)
            away_score = score_by_name.get(match.away_team.name)
            if home_score is None or away_score is None:
                continue

            try:
                home_goals = int(home_score)
                away_goals = int(away_score)
            except (TypeError, ValueError):
                continue

            finish_match(match, home_goals, away_goals)
            count += 1

    if count:
        db.session.commit()

    return count


def fetch_historical_odds(
    sport: str, date_iso: str, regions: str = "eu,uk", markets: str = "h2h"
) -> list[dict]:
    """Snapshot of odds as they stood at `date_iso` (ISO8601 UTC timestamp).

    Requires a PAID the-odds-api plan - returns 401/403 on the free tier -
    and costs ~10x a live /odds request per call, so callers should batch
    by unique timestamp rather than querying per match.
    """
    if not _api_enabled():
        return []

    try:
        r = requests.get(
            f"{BASE_URL}/historical/sports/{sport}/odds",
            params={
                "apiKey": Config.ODDS_API_KEY,
                "regions": regions,
                "markets": markets,
                "date": date_iso,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("data") or []
    except requests.RequestException:
        return []


def _find_event_for_match(events: list[dict], match: Match) -> dict | None:
    for ev in events:
        if ev.get("home_team") == match.home_team.name and ev.get("away_team") == match.away_team.name:
            return ev

    norm_home = normalize_team_name(match.home_team.name)
    norm_away = normalize_team_name(match.away_team.name)
    for ev in events:
        if (
            normalize_team_name(ev.get("home_team", "")) == norm_home
            and normalize_team_name(ev.get("away_team", "")) == norm_away
        ):
            return ev
    return None


def backfill_historical_odds(max_requests: int = 50) -> int:
    """Attach real bookmaker odds to already-known finished matches that
    don't have any yet (the ones backfilled from TheSportsDB/football-data.org,
    neither of which provides odds), using the-odds-api's historical
    snapshot endpoint. This is what makes `/backtest` actually able to
    compute ROI instead of just accuracy.

    Requires a paid the-odds-api plan (see `fetch_historical_odds`). Costly
    if run unbounded - one request per distinct kickoff timestamp, at ~10x
    the cost of a live odds request - so `max_requests` caps how many
    snapshot calls happen in a single run. Safe to call repeatedly across
    sessions: matches that already have odds are skipped, so each run just
    picks up where the last one left off within whatever budget you want to
    spend.
    """
    if not _api_enabled():
        return 0

    sport_by_league = {v: k for k, v in SPORTS_MAP.items()}

    pending = (
        Match.query.filter(Match.status == "finished", Match.home_goals.isnot(None))
        .order_by(Match.match_date.asc())
        .all()
    )
    pending = [m for m in pending if not Odds.query.filter_by(match_id=m.id).first()]
    if not pending:
        return 0

    groups: dict[tuple[str, str], list[Match]] = {}
    for m in pending:
        sport = sport_by_league.get(m.league)
        if not sport:
            continue
        # the-odds-api's historical endpoint requires a literal "Z" suffix -
        # datetime.isoformat() produces "+00:00", which it rejects as invalid.
        date_iso = m.match_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        groups.setdefault((sport, date_iso), []).append(m)

    total = 0
    requests_made = 0
    for (sport, date_iso), group_matches in groups.items():
        if requests_made >= max_requests:
            break

        events = fetch_historical_odds(sport, date_iso)
        requests_made += 1
        time.sleep(1)

        for m in group_matches:
            ev = _find_event_for_match(events, m)
            if not ev:
                continue

            home_name = ev.get("home_team", "")
            away_name = ev.get("away_team", "")

            for bm in ev.get("bookmakers", []):
                bookmaker_name = bm.get("title", "Unknown")
                for market in bm.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                    if home_name not in outcomes or away_name not in outcomes:
                        continue

                    odds = Odds(
                        match_id=m.id,
                        bookmaker=bookmaker_name,
                        home_odds=outcomes[home_name],
                        draw_odds=outcomes.get("Draw", 1.0),
                        away_odds=outcomes[away_name],
                        timestamp=m.match_date,
                    )
                    db.session.add(odds)
                    total += 1

    if total:
        db.session.commit()

    return total
