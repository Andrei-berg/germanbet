from __future__ import annotations
import re
import unicodedata
from datetime import timedelta
from app import db
from app.models import Team, Match
from app.aggregator.settlement import finish_match


_CLUB_SUFFIX_TOKENS = {"fc", "cf", "afc", "ac", "sc", "ssc", "us", "as", "calcio"}

# Same failure mode as RPL_NAME_ALIASES (api_football_client.py), just for the
# other 5 leagues: normalize_team_name's suffix-stripping isn't enough when
# providers disagree on the *whole* name, not just a suffix - e.g. API-Football's
# "Tottenham" vs football-data.org's "Tottenham Hotspur" share no stripped
# token in common. Discovered 2026-07-22 when an API-Football top-5-league
# backfill created ~70 duplicate Team rows (and ~1200 duplicate/fragmented
# Match rows, double-counting Elo for real fixtures) this way. Hand-verified
# against real club identities - deliberately excludes reserve/youth/women's
# teams that share a name root with the first team (e.g. "Real Valladolid
# Promesas" is Valladolid's reserve side, not the same competitive entity).
TOP5_NAME_ALIASES = {
    # Premier League
    "Brighton & Hove Albion FC": "Brighton", "Brighton and Hove Albion": "Brighton",
    "Ipswich Town": "Ipswich", "Leeds United": "Leeds", "Newcastle United": "Newcastle",
    "Sheffield United": "Sheffield Utd", "Tottenham Hotspur": "Tottenham",
    "West Ham United": "West Ham", "Wolverhampton Wanderers": "Wolves",
    # Bundesliga
    "1. FC Heidenheim 1846": "1. FC Heidenheim", "FC Heidenheim": "1. FC Heidenheim",
    "FC Köln": "1. FC Köln", "TSG 1899 Hoffenheim": "1899 Hoffenheim",
    "TSG Hoffenheim": "1899 Hoffenheim", "Bayer 04 Leverkusen": "Bayer Leverkusen",
    "Bayern Munich": "FC Bayern München", "Elversberg": "SV Elversberg",
    "FC St. Pauli 1910": "St Pauli", "Hamburg": "Hamburger SV",
    "1. FSV Mainz 05": "FSV Mainz 05", "Mainz": "FSV Mainz 05",
    "SC Paderborn": "SC Paderborn 07", "SV Werder Bremen": "Werder Bremen",
    "1. FC Union Berlin": "Union Berlin", "Wolfsburg": "VfL Wolfsburg",
    # La Liga
    "Athletic Bilbao": "Athletic Club", "Deportivo Alavés": "Alavés",
    "Club Atlético de Madrid": "Atlético Madrid", "RC Celta de Vigo": "Celta Vigo",
    "CA Osasuna": "Osasuna", "RCD Espanyol de Barcelona": "Espanyol",
    "Levante UD": "Levante", "RCD Mallorca": "Mallorca", "Oviedo": "Real Oviedo",
    "Rayo Vallecano de Madrid": "Rayo Vallecano", "Real Betis Balompié": "Real Betis",
    "Real Sociedad de Fútbol": "Real Sociedad",
    # Serie A
    "Pisa": "AC Pisa 1909", "ACF Fiorentina": "Fiorentina", "Atalanta BC": "Atalanta",
    "Bologna FC 1909": "Bologna", "Como 1907": "Como",
    "FC Internazionale Milano": "Inter", "Inter Milan": "Inter",
    "Genoa CFC": "Genoa", "SS Lazio": "Lazio", "Parma Calcio 1913": "Parma",
    # Ligue 1
    "AJ Auxerre": "Auxerre", "Angers SCO": "Angers", "Brest": "Stade Brestois 29",
    "Troyes": "Estac Troyes", "RC Lens": "Lens", "Racing Club de Lens": "Lens",
    "Lille OSC": "Lille", "Olympique Lyonnais": "Lyon", "Olympique de Marseille": "Marseille",
    "OGC Nice": "Nice", "Nîmes Olympique": "Nimes", "Red Star": "RED Star FC 93",
    "Stade de Reims": "Reims", "Stade Rennais FC 1901": "Rennes",
    "RC Strasbourg Alsace": "Strasbourg",
}


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


def canonical_team_name(name: str) -> str:
    """Map a provider-specific alternate name to the name our Team row was
    created under (see TOP5_NAME_ALIASES). Apply before any Team lookup so
    all providers converge on one row instead of forking a duplicate.
    """
    return TOP5_NAME_ALIASES.get(name, name)


def set_team_id_if_unclaimed(team: Team, id_field: str, ext_id: str | None) -> None:
    """Attach a provider id to a resolved team, unless some *other* row
    already claims it - two rows independently ending up with the same
    provider id for the same real club (e.g. TheSportsDB's "Racing de
    Santander" vs "Real Racing Club de Santander") would otherwise hit the
    column's UNIQUE constraint and crash the whole sync. Leaving the id
    unset on this row is a safe no-op: it just means this specific row won't
    get a fast id-lookup next time, not silent data corruption.
    """
    if not ext_id or getattr(team, id_field):
        return
    conflict = Team.query.filter_by(**{id_field: ext_id}).first()
    if conflict and conflict.id != team.id:
        return
    setattr(team, id_field, ext_id)
    db.session.flush()


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

    name = canonical_team_name(name)

    team = Team.query.filter_by(name=name).first()
    if team:
        set_team_id_if_unclaimed(team, id_field, ext_id)
        return team

    normalized_target = normalize_team_name(name)
    for candidate in Team.query.filter_by(league=league).all():
        if normalize_team_name(candidate.name) == normalized_target:
            set_team_id_if_unclaimed(candidate, id_field, ext_id)
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
