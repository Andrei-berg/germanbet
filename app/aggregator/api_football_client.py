from __future__ import annotations

import time
import requests
from datetime import datetime, timedelta, timezone
from app import db
from app.models import Match, Odds
from app.config import Config
from app.aggregator.historical_backfill import replay_collected_events, normalize_team_name


BASE_URL = "https://v3.football.api-sports.io"

REQUEST_DELAY_SECONDS = 0.5  # Pro plan (7500/day) is generous, still be polite

LEAGUE_IDS = {
    "Premier League": 39,
    "Bundesliga": 78,
    "La Liga": 140,
    "Serie A": 135,
    "Ligue 1": 61,
    "Russian Premier League": 235,
}

# API-Football uses shorter/different club names than the-odds-api, which is
# what our Team rows are named after (e.g. "Zenit" vs "Zenit St Petersburg",
# "Dynamo" vs "Dinamo Moscow"). A generic fuzzy/substring matcher is risky
# here specifically - RPL has two distinct real "Dynamo" clubs (Moscow and
# Makhachkala), so a token-subset heuristic could silently merge two
# different clubs' histories. Hand-verified against both providers' current
# rosters instead (2026-2027 season) - safe because RPL is only ~16 teams.
RPL_NAME_ALIASES = {
    "Zenit": "Zenit St Petersburg",
    "Lokomotiv": "Lokomotiv Moscow",
    "FC Rostov": "FK Rostov",
    "Krylia Sovetov": "Kryliya Sovetov",
    "FC Orenburg": "Gazovik Orenburg",
    "Rubin": "Rubin Kazan",
    "Akhmat": "FC Akhmat Grozny",
    "Dynamo": "Dinamo Moscow",
    "Fakel": "FC Fakel Voronezh",
    "Baltika": "FC Baltika Kaliningrad",
    "Akron": "FC Akron Tolyatti",
    "Dinamo Makhachkala": "FC Dynamo Makhachkala",
    "Rodina Moskva": "Rodina Moscow",
}


def _api_enabled() -> bool:
    return bool(Config.API_FOOTBALL_KEY) and Config.API_FOOTBALL_KEY != "test"


def _headers() -> dict:
    return {"x-apisports-key": Config.API_FOOTBALL_KEY}


def _canonical_name(raw_name: str, league_name: str) -> str:
    if league_name == "Russian Premier League":
        return RPL_NAME_ALIASES.get(raw_name, raw_name)
    return raw_name


def fetch_fixtures(
    league_id: int, season: int, status: str | None = None, date: str | None = None
) -> list[dict]:
    if not _api_enabled():
        return []

    params = {"league": league_id, "season": season}
    if status:
        params["status"] = status
    if date:
        params["date"] = date

    try:
        r = requests.get(f"{BASE_URL}/fixtures", headers=_headers(), params=params, timeout=20)
        r.raise_for_status()
        return r.json().get("response") or []
    except requests.RequestException:
        return []


def fetch_odds_by_date(date_iso: str, league_id: int, season: int) -> list[dict]:
    if not _api_enabled():
        return []

    try:
        r = requests.get(
            f"{BASE_URL}/odds",
            headers=_headers(),
            params={"date": date_iso, "league": league_id, "season": season},
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("response") or []
    except requests.RequestException:
        return []


def _collect_fixture(raw: dict, league_name: str, collected: dict[str, dict]) -> None:
    status = (raw.get("fixture", {}).get("status") or {}).get("short")
    if status != "FT":
        return

    event_id = raw.get("fixture", {}).get("id")
    home = raw.get("teams", {}).get("home") or {}
    away = raw.get("teams", {}).get("away") or {}
    home_name = _canonical_name((home.get("name") or "").strip(), league_name)
    away_name = _canonical_name((away.get("name") or "").strip(), league_name)
    home_ext_id = home.get("id")
    away_ext_id = away.get("id")

    goals = raw.get("goals") or {}
    home_goals = goals.get("home")
    away_goals = goals.get("away")

    date_str = raw.get("fixture", {}).get("date")
    event_date = None
    if date_str:
        try:
            event_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            event_date = None

    if not (event_id and home_name and away_name and event_date):
        return
    if home_goals is None or away_goals is None:
        return

    prefixed_id = f"af-{event_id}"
    if prefixed_id in collected:
        return

    collected[prefixed_id] = {
        "home_name": home_name,
        "away_name": away_name,
        "home_ext_id": f"af-team-{home_ext_id}" if home_ext_id else None,
        "away_ext_id": f"af-team-{away_ext_id}" if away_ext_id else None,
        "home_goals": int(home_goals),
        "away_goals": int(away_goals),
        "date": event_date,
        "league": league_name,
    }


def backfill_season_results(season: int, leagues: dict[str, int] | None = None) -> int:
    """Warm start using API-Football's per-league season fixtures - by
    default just Russian Premier League, since neither TheSportsDB's free
    tier nor football-data.org cover it at all, while the other 5 leagues
    already have solid history from football-data.org (re-running them here
    would just cost quota re-discovering matches that dedupe away).
    """
    if not _api_enabled():
        return 0

    leagues = leagues if leagues is not None else {"Russian Premier League": LEAGUE_IDS["Russian Premier League"]}
    collected: dict[str, dict] = {}

    for league_name, league_id in leagues.items():
        fixtures = fetch_fixtures(league_id, season, status="FT")
        time.sleep(REQUEST_DELAY_SECONDS)
        for raw in fixtures:
            _collect_fixture(raw, league_name, collected)

    return replay_collected_events(collected, id_field="api_football_id")


def _find_scheduled_match(home_name: str, away_name: str, event_date, league_name: str) -> Match | None:
    norm_home = normalize_team_name(home_name)
    norm_away = normalize_team_name(away_name)
    window_start = event_date - timedelta(hours=6)
    window_end = event_date + timedelta(hours=6)

    candidates = Match.query.filter(
        Match.league == league_name,
        Match.status == "scheduled",
        Match.match_date >= window_start,
        Match.match_date <= window_end,
    ).all()
    for m in candidates:
        if (
            normalize_team_name(m.home_team.name) == norm_home
            and normalize_team_name(m.away_team.name) == norm_away
        ):
            return m
    return None


def sync_upcoming_odds(days_ahead: int = 10, season: int = 2026, leagues: dict[str, int] | None = None) -> int:
    """Enriches *existing* scheduled matches (already created by
    odds_client.sync_odds_from_api) with API-Football's bookmaker odds,
    rather than creating new Match rows - the-odds-api already owns fixture
    creation. This matters most for Russian Premier League, where
    the-odds-api's own bookmaker coverage is thin (2-5 books vs 30+ for the
    top-5 European leagues), so a wider consensus median is worth having.
    """
    if not _api_enabled():
        return 0

    leagues = leagues if leagues is not None else {"Russian Premier League": LEAGUE_IDS["Russian Premier League"]}
    today = datetime.now(timezone.utc).date()
    total = 0

    for league_name, league_id in leagues.items():
        for day_offset in range(days_ahead):
            date_str = (today + timedelta(days=day_offset)).isoformat()

            fixtures = fetch_fixtures(league_id, season, date=date_str)
            time.sleep(REQUEST_DELAY_SECONDS)
            fixtures_by_id = {f["fixture"]["id"]: f for f in fixtures}

            odds_events = fetch_odds_by_date(date_str, league_id, season)
            time.sleep(REQUEST_DELAY_SECONDS)

            for ev in odds_events:
                fixture_id = ev.get("fixture", {}).get("id")
                fixture = fixtures_by_id.get(fixture_id)
                if not fixture:
                    continue

                home_name = _canonical_name(fixture["teams"]["home"]["name"], league_name)
                away_name = _canonical_name(fixture["teams"]["away"]["name"], league_name)
                event_date_str = fixture["fixture"]["date"]
                try:
                    event_date = datetime.fromisoformat(event_date_str.replace("Z", "+00:00")).astimezone(timezone.utc)
                except ValueError:
                    continue

                match = _find_scheduled_match(home_name, away_name, event_date, league_name)
                if not match:
                    continue

                for bm in ev.get("bookmakers", []):
                    bookmaker_name = bm.get("name", "Unknown")
                    for bet in bm.get("bets", []):
                        if bet.get("name") != "Match Winner":
                            continue

                        values = {v["value"]: v["odd"] for v in bet.get("values", [])}
                        if not all(k in values for k in ("Home", "Draw", "Away")):
                            continue

                        try:
                            odds = Odds(
                                match_id=match.id,
                                bookmaker=f"{bookmaker_name} (API-Football)",
                                home_odds=float(values["Home"]),
                                draw_odds=float(values["Draw"]),
                                away_odds=float(values["Away"]),
                            )
                        except (TypeError, ValueError):
                            continue
                        db.session.add(odds)
                        total += 1

    if total:
        db.session.commit()

    return total
