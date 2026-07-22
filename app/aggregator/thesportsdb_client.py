from __future__ import annotations

import time
import requests
from datetime import datetime, timezone, timedelta
from app import db
from app.models import Team, Match
from app.config import Config
from app.aggregator.settlement import finish_match
from app.aggregator.historical_backfill import replay_collected_events, set_team_id_if_unclaimed


BASE_URL = "https://www.thesportsdb.com/api/v1/json"


def _api_enabled() -> bool:
    return bool(Config.SPORTSDB_API_KEY) and Config.SPORTSDB_API_KEY != "test"


def fetch_teams_by_league(league_name: str, sport: str = "Soccer") -> list[dict]:
    if not _api_enabled():
        return []

    try:
        r = requests.get(
            f"{BASE_URL}/{Config.SPORTSDB_API_KEY}/search_all_teams.php",
            params={"l": league_name, "s": sport},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("teams", []) or []
    except requests.RequestException:
        return []


def fetch_events_by_league(league_name: str, season: str = "") -> list[dict]:
    if not _api_enabled():
        return []

    try:
        r = requests.get(
            f"{BASE_URL}/{Config.SPORTSDB_API_KEY}/search_all_events.php",
            params={"l": league_name, "s": season if season else str(datetime.now().year)},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("events", []) or []
    except requests.RequestException:
        return []


def fetch_team_by_name(name: str) -> dict | None:
    if not _api_enabled():
        return None

    try:
        r = requests.get(
            f"{BASE_URL}/{Config.SPORTSDB_API_KEY}/searchteams.php",
            params={"t": name},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        teams = data.get("teams") or []
        return teams[0] if teams else None
    except requests.RequestException:
        return None


def fetch_last_events_for_team(team_external_id: str) -> list[dict]:
    if not _api_enabled():
        return []

    try:
        r = requests.get(
            f"{BASE_URL}/{Config.SPORTSDB_API_KEY}/eventslast.php",
            params={"id": team_external_id},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("results") or []
    except requests.RequestException:
        return []


def fetch_league_id(league_name: str) -> str | None:
    if not _api_enabled():
        return None

    try:
        r = requests.get(
            f"{BASE_URL}/{Config.SPORTSDB_API_KEY}/all_leagues.php",
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        for lg in data.get("leagues") or []:
            if lg.get("strLeague") == league_name:
                return lg.get("idLeague")
        return None
    except requests.RequestException:
        return None


def fetch_season_events(league_id: str, season: str) -> list[dict]:
    if not _api_enabled():
        return []

    try:
        r = requests.get(
            f"{BASE_URL}/{Config.SPORTSDB_API_KEY}/eventsseason.php",
            params={"id": league_id, "s": season},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("events") or []
    except requests.RequestException:
        return []


def fetch_lookup_team(team_id: str) -> dict | None:
    if not _api_enabled():
        return None

    try:
        r = requests.get(
            f"{BASE_URL}/{Config.SPORTSDB_API_KEY}/lookupteam.php",
            params={"id": team_id},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        teams = data.get("teams", [])
        return teams[0] if teams else None
    except requests.RequestException:
        return None


def sync_teams_from_thesportsdb() -> int:
    if not _api_enabled():
        return 0

    leagues = ["English Premier League", "German Bundesliga", "Russian Premier League"]
    count = 0

    for league in leagues:
        teams_data = fetch_teams_by_league(league)
        for td in teams_data:
            name = td.get("strTeam", "").strip()
            if not name:
                continue

            existing = Team.query.filter_by(name=name).first()
            if existing:
                continue

            team = Team(
                name=name,
                country=td.get("strCountry", ""),
                league=td.get("strLeague", league),
                external_id=td.get("idTeam"),
            )
            db.session.add(team)
            count += 1

    if count:
        db.session.commit()

    return count


def sync_matches_from_thesportsdb() -> int:
    if not _api_enabled():
        return 0

    leagues = {
        "English Premier League": "4328",
        "German Bundesliga": "4331",
    }
    count = 0

    for league_name, league_id in leagues.items():
        try:
            r = requests.get(
                f"{BASE_URL}/{Config.SPORTSDB_API_KEY}/eventsnextleague.php",
                params={"id": league_id},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            events = data.get("events", []) or []

            for ev in events:
                home_name = ev.get("strHomeTeam", "").strip()
                away_name = ev.get("strAwayTeam", "").strip()
                if not home_name or not away_name:
                    continue

                home_team = Team.query.filter_by(name=home_name).first()
                away_team = Team.query.filter_by(name=away_name).first()
                if not home_team or not away_team:
                    continue

                date_str = ev.get("dateEvent", "")
                time_str = ev.get("strTime", "")
                event_date = _parse_datetime(date_str, time_str)

                if not event_date:
                    continue

                existing = Match.query.filter_by(external_id=ev.get("idEvent")).first()
                if existing:
                    continue

                match = Match(
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                    league=league_name,
                    match_date=event_date,
                    status="scheduled",
                    external_id=ev.get("idEvent"),
                )
                db.session.add(match)
                count += 1

        except requests.RequestException:
            continue

    if count:
        db.session.commit()

    return count


def fetch_event_result(external_id: str) -> dict | None:
    if not _api_enabled():
        return None

    try:
        r = requests.get(
            f"{BASE_URL}/{Config.SPORTSDB_API_KEY}/lookupevent.php",
            params={"id": external_id},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        events = data.get("events", [])
        if not events:
            return None
        ev = events[0]
        status = (ev.get("strStatus") or ev.get("strProgress", "")).strip()
        home_goals = ev.get("intHomeScore")
        away_goals = ev.get("intAwayScore")
        if home_goals is not None and away_goals is not None:
            return {
                "home_goals": int(home_goals),
                "away_goals": int(away_goals),
                "status": status,
            }
        if "finished" in status.lower() or "final" in status.lower():
            home_goals = int(ev.get("intHomeScore", 0) or 0)
            away_goals = int(ev.get("intAwayScore", 0) or 0)
            return {
                "home_goals": home_goals,
                "away_goals": away_goals,
                "status": status,
            }
        return None
    except requests.RequestException:
        return None


def sync_finished_matches() -> int:
    """Fetch real results for matches that came from TheSportsDB (i.e. carry
    a TheSportsDB external_id). Matches sourced from the-odds-api instead use
    `odds_client.sync_results_from_odds_api()` - there is no fallback here
    that invents a score for a match nobody has confirmed the result of.
    """
    if not _api_enabled():
        return 0

    count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=3)
    past_matches = Match.query.filter(
        Match.status == "scheduled",
        Match.match_date <= cutoff,
        Match.external_id.isnot(None),
    ).all()

    for m in past_matches:
        result = fetch_event_result(m.external_id)
        if result:
            finish_match(m, result["home_goals"], result["away_goals"])
            count += 1

    if count:
        db.session.commit()

    return count


REQUEST_DELAY_SECONDS = 1.5  # stay well under TheSportsDB's free-tier rate limit

# Our internal league label (matches Team.league / the-odds-api's naming) ->
# TheSportsDB's own league name, needed to look up its league id.
THESPORTSDB_LEAGUE_NAMES = {
    "Premier League": "English Premier League",
    "Bundesliga": "German Bundesliga",
    "La Liga": "Spanish La Liga",
    "Serie A": "Italian Serie A",
    "Ligue 1": "French Ligue 1",
    "Russian Premier League": "Russian Premier League",
}


def _collect_event(ev: dict, league_name: str, collected: dict[str, dict]) -> None:
    event_id = ev.get("idEvent")
    home_goals = ev.get("intHomeScore")
    away_goals = ev.get("intAwayScore")
    home_name = (ev.get("strHomeTeam") or "").strip()
    away_name = (ev.get("strAwayTeam") or "").strip()
    # TheSportsDB's own team names for this fixture may not match how
    # the-odds-api named the same club when it created our Team row.
    # Carrying the TheSportsDB team ids lets us resolve to *that* row
    # instead of name-matching into a duplicate.
    home_ext_id = ev.get("idHomeTeam")
    away_ext_id = ev.get("idAwayTeam")
    event_date = _parse_datetime(ev.get("dateEvent", ""), ev.get("strTime", ""))

    if not (event_id and home_name and away_name and event_date):
        return
    if home_goals is None or away_goals is None:
        return
    if event_id in collected:
        return

    collected[event_id] = {
        "home_name": home_name,
        "away_name": away_name,
        "home_ext_id": home_ext_id,
        "away_ext_id": away_ext_id,
        "home_goals": int(home_goals),
        "away_goals": int(away_goals),
        "date": event_date,
        "league": league_name,
    }


def backfill_season_results(season: str) -> int:
    """Warm start using a whole season's fixtures per league in one shot
    (up to ~380 matches per league) instead of TheSportsDB's per-team
    "last 5 events" endpoint, which has a hard ceiling that re-querying
    can't get past - most teams only ever picked up 1-3 matches from it
    even after many retries, because a team only gets its own dedicated
    "last 5" look-up once, and otherwise only appears when discovered as
    someone else's opponent.

    Only 6 requests total (one per league) rather than ~2 per team, so this
    comfortably avoids TheSportsDB's rate limit and gives every team real,
    honest match history right away instead of a thin, capped sample.
    """
    if not _api_enabled():
        return 0

    collected: dict[str, dict] = {}

    for league_name, thesportsdb_name in THESPORTSDB_LEAGUE_NAMES.items():
        league_id = fetch_league_id(thesportsdb_name)
        time.sleep(REQUEST_DELAY_SECONDS)
        if not league_id:
            continue

        events = fetch_season_events(league_id, season)
        time.sleep(REQUEST_DELAY_SECONDS)
        for ev in events:
            _collect_event(ev, league_name, collected)

    return replay_collected_events(collected, id_field="external_id")


def backfill_team_history(limit_per_team: int = 5, max_teams_per_run: int = 20) -> int:
    """Per-team warm start, kept as a fallback/top-up for teams
    `backfill_season_results` didn't cover (e.g. a club that just got
    promoted mid-season, or a league not in THESPORTSDB_LEAGUE_NAMES).

    Filters on `matches_played < limit_per_team` rather than `== 0`: a team
    often picks up 1-2 matches just by being discovered as someone else's
    opponent, and if we stopped crawling it the moment that happened it
    would get permanently stuck short of its own full history. Existing
    matches are de-duplicated by TheSportsDB event id, so re-fetching a
    team's last events after it already has some is safe - but note
    eventslast.php only ever returns up to ~5 events per team no matter how
    often it's called, so this alone won't take a team past that.

    Idempotent and cheap to re-run - only processes up to `max_teams_per_run`
    teams per call (1-2 requests each) and sleeps between requests, since
    TheSportsDB's free tier rate-limits (HTTP 429) a burst of requests across
    ~100+ teams in one go; the remainder is picked up on the next tick.
    """
    if not _api_enabled():
        return 0

    teams = (
        Team.query.filter(Team.matches_played < limit_per_team)
        .limit(max_teams_per_run)
        .all()
    )
    if not teams:
        return 0

    collected: dict[str, dict] = {}  # keyed by TheSportsDB event id, dedups shared fixtures

    for team in teams:
        if not team.external_id:
            found = fetch_team_by_name(team.name)
            time.sleep(REQUEST_DELAY_SECONDS)
            if not found:
                continue
            # Two of our rows can independently name-match the same
            # TheSportsDB team (e.g. "Racing de Santander" vs "Real Racing
            # Club de Santander") - don't blindly claim an id another row
            # already owns, that hits external_id's UNIQUE constraint.
            set_team_id_if_unclaimed(team, "external_id", found.get("idTeam"))
            if not team.external_id:
                continue

        events = fetch_last_events_for_team(team.external_id)[:limit_per_team]
        time.sleep(REQUEST_DELAY_SECONDS)
        for ev in events:
            _collect_event(ev, team.league, collected)

    if not collected:
        db.session.commit()  # persist any external_id lookups even with no usable events
        return 0

    return replay_collected_events(collected, id_field="external_id")


def _parse_datetime(date_str: str, time_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        if time_str:
            dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        else:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
