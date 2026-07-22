from __future__ import annotations

import time
import requests
from datetime import datetime, timezone
from app.config import Config
from app.aggregator.historical_backfill import replay_collected_events


BASE_URL = "https://api.football-data.org/v4"

REQUEST_DELAY_SECONDS = 6.5  # free tier is capped at 10 requests/minute

# Our internal league label -> football-data.org's competition code.
# No Russian Premier League: football-data.org's free tier doesn't cover it.
COMPETITION_CODES = {
    "Premier League": "PL",
    "Bundesliga": "BL1",
    "La Liga": "PD",
    "Serie A": "SA",
    "Ligue 1": "FL1",
}


def _api_enabled() -> bool:
    return bool(Config.FOOTBALL_DATA_API_KEY) and Config.FOOTBALL_DATA_API_KEY != "test"


def fetch_competition_matches(code: str, season: str, status: str = "FINISHED") -> list[dict]:
    if not _api_enabled():
        return []

    try:
        r = requests.get(
            f"{BASE_URL}/competitions/{code}/matches",
            headers={"X-Auth-Token": Config.FOOTBALL_DATA_API_KEY},
            params={"season": season, "status": status},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("matches") or []
    except requests.RequestException:
        return []


def _collect_match(raw: dict, league_name: str, collected: dict[str, dict]) -> None:
    if raw.get("status") != "FINISHED":
        return

    event_id = raw.get("id")
    home = raw.get("homeTeam") or {}
    away = raw.get("awayTeam") or {}
    home_name = (home.get("name") or "").strip()
    away_name = (away.get("name") or "").strip()
    home_team_ext_id = home.get("id")
    away_team_ext_id = away.get("id")

    score = (raw.get("score") or {}).get("fullTime") or {}
    home_goals = score.get("home")
    away_goals = score.get("away")

    utc_date = raw.get("utcDate")
    event_date = None
    if utc_date:
        try:
            event_date = datetime.fromisoformat(utc_date.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            event_date = None

    if not (event_id and home_name and away_name and event_date):
        return
    if home_goals is None or away_goals is None:
        return

    # Prefix so these ids can never collide with TheSportsDB's own id space
    # in the shared Match.external_id / Team.football_data_id columns.
    prefixed_event_id = f"fd-{event_id}"
    if prefixed_event_id in collected:
        return

    collected[prefixed_event_id] = {
        "home_name": home_name,
        "away_name": away_name,
        "home_ext_id": f"fd-team-{home_team_ext_id}" if home_team_ext_id else None,
        "away_ext_id": f"fd-team-{away_team_ext_id}" if away_team_ext_id else None,
        "home_goals": int(home_goals),
        "away_goals": int(away_goals),
        "date": event_date,
        "league": league_name,
    }


def backfill_season_results(season: str) -> int:
    """Warm start using football-data.org's per-competition season results -
    a real, un-capped full season per request (unlike TheSportsDB's free
    tier, which truncates to ~15 events regardless of what's asked for and
    doesn't cover La Liga/Ligue 1/RPL at all on the free plan).

    Still no Russian Premier League here either (not on football-data.org's
    free tier) - RPL team history keeps coming only from real settled
    fixtures via odds_client.sync_results_from_odds_api().
    """
    if not _api_enabled():
        return 0

    collected: dict[str, dict] = {}

    for league_name, code in COMPETITION_CODES.items():
        matches = fetch_competition_matches(code, season)
        time.sleep(REQUEST_DELAY_SECONDS)
        for raw in matches:
            _collect_match(raw, league_name, collected)

    return replay_collected_events(collected, id_field="football_data_id")
