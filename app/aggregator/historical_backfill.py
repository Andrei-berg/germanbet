from __future__ import annotations
import re
import unicodedata
from datetime import timedelta
from app import db
from app.models import Team, Match
from app.aggregator.settlement import finish_match


_CLUB_SUFFIX_TOKENS = {"fc", "cf", "afc", "ac", "sc", "ssc", "us", "as", "calcio"}


def normalize_team_name(name: str) -> str:
    """Strip diacritics/case/common club-suffix tokens (FC, CF, AFC, ...) so
    the same real club can be recognized across providers that name it
    differently, e.g. "Manchester City" (the-odds-api) vs "Manchester City
    FC" (football-data.org) vs "VfB Stuttgart" (odds) vs "Stuttgart"
    (TheSportsDB).
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    tokens = re.findall(r"[a-z0-9]+", ascii_name.lower())
    tokens = [t for t in tokens if t not in _CLUB_SUFFIX_TOKENS]
    return " ".join(tokens)


def resolve_historical_team(name: str, ext_id: str | None, league: str, id_field: str) -> Team:
    """Find-or-create a Team for a historical fixture, preferring a match on
    the source's own id (`id_field`, e.g. "external_id" for TheSportsDB,
    "football_data_id" for football-data.org) over name matching, then an
    exact name match, then a normalized-name match within the same league.
    Different providers name the same real club differently, so name-only
    exact matching silently creates duplicate teams.
    """
    if ext_id:
        existing = Team.query.filter_by(**{id_field: ext_id}).first()
        if existing:
            return existing

    team = Team.query.filter_by(name=name).first()
    if team:
        if ext_id and not getattr(team, id_field):
            setattr(team, id_field, ext_id)
            db.session.flush()
        return team

    normalized_target = normalize_team_name(name)
    for candidate in Team.query.filter_by(league=league).all():
        if normalize_team_name(candidate.name) == normalized_target:
            if ext_id and not getattr(candidate, id_field):
                setattr(candidate, id_field, ext_id)
                db.session.flush()
            return candidate

    team = Team(name=name, league=league, **{id_field: ext_id})
    db.session.add(team)
    db.session.flush()
    return team


def _same_fixture_already_recorded(home_team_id: int, away_team_id: int, match_date) -> bool:
    """Catches the same real-world fixture being re-discovered from a
    *different* source (e.g. both TheSportsDB and football-data.org cover
    the same league), where the source-specific event id won't match. A
    +/-1 day window absorbs minor timezone/precision differences between
    providers' kickoff timestamps.
    """
    window_start = match_date - timedelta(days=1)
    window_end = match_date + timedelta(days=1)
    return (
        Match.query.filter(
            Match.home_team_id == home_team_id,
            Match.away_team_id == away_team_id,
            Match.match_date >= window_start,
            Match.match_date <= window_end,
            Match.status == "finished",
        ).first()
        is not None
    )


def replay_collected_events(collected: dict[str, dict], id_field: str) -> int:
    """Replay a batch of normalized historical events (see the shape built
    by each source's own collector) in chronological order through
    finish_match(), so Elo updates apply in the right sequence.

    Each event dict must have: home_name, away_name, home_ext_id, away_ext_id,
    home_goals, away_goals, date, league.
    """
    if not collected:
        return 0

    count = 0
    for event_id, ev in sorted(collected.items(), key=lambda kv: kv[1]["date"]):
        if Match.query.filter_by(external_id=event_id).first():
            continue

        home_team = resolve_historical_team(ev["home_name"], ev["home_ext_id"], ev["league"], id_field)
        away_team = resolve_historical_team(ev["away_name"], ev["away_ext_id"], ev["league"], id_field)

        if _same_fixture_already_recorded(home_team.id, away_team.id, ev["date"]):
            continue

        match = Match(
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            league=ev["league"],
            match_date=ev["date"],
            status="scheduled",
            external_id=event_id,
        )
        db.session.add(match)
        db.session.flush()

        finish_match(match, ev["home_goals"], ev["away_goals"])
        count += 1

    db.session.commit()
    return count
