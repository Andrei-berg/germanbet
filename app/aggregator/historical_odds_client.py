from __future__ import annotations

import csv
import io
import requests
from datetime import datetime, timedelta, timezone
from app import db
from app.models import Match, Odds


BASE_URL = "https://www.football-data.co.uk"

# football-data.co.uk abbreviates team names heavily and inconsistently
# ("Dortmund", "Ath Madrid", "M'gladbach", "Paris SG") - reconciling that
# against our Team rows would need a large hand-built alias table per
# league. Instead we match on (league, date window, exact final score):
# since we already have the real result for these matches (from
# football-data.org / API-Football), a matching scoreline within a day of
# the reported date is a solid, naming-agnostic fingerprint for "same real
# fixture" - and if it's not unique, we just skip that row rather than guess.
BIG5_LEAGUE_CODES = {
    "Premier League": "E0",
    "Bundesliga": "D1",
    "La Liga": "SP1",
    "Serie A": "I1",
    "Ligue 1": "F1",
}

RUSSIA_CSV_URL = f"{BASE_URL}/new/RUS.csv"


def _fetch_csv_rows(url: str) -> list[dict]:
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        text = r.content.decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))
    except requests.RequestException:
        return []


def _parse_date(date_str: str, time_str: str = "") -> datetime | None:
    if not date_str:
        return None

    parsed = None
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            parsed = datetime.strptime(date_str, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        return None

    if time_str:
        try:
            t = datetime.strptime(time_str, "%H:%M").time()
            parsed = parsed.replace(hour=t.hour, minute=t.minute)
        except ValueError:
            pass

    return parsed.replace(tzinfo=timezone.utc)


def _find_existing_match(league_name: str, event_date, home_goals: int, away_goals: int) -> Match | None:
    window_start = event_date - timedelta(days=1)
    window_end = event_date + timedelta(days=1)
    candidates = Match.query.filter(
        Match.league == league_name,
        Match.status == "finished",
        Match.match_date >= window_start,
        Match.match_date <= window_end,
        Match.home_goals == home_goals,
        Match.away_goals == away_goals,
    ).all()
    return candidates[0] if len(candidates) == 1 else None


def _get_float(row: dict, *keys: str) -> float | None:
    for k in keys:
        v = row.get(k)
        if v:
            try:
                return float(v)
            except ValueError:
                continue
    return None


def _attach_odds_row(match: Match, row: dict) -> int:
    # Prefer closing lines ("...C..." columns, the final pre-match price);
    # fall back to the plain columns for sources that only have one set
    # (the Russia "extra league" file has no separate opening odds).
    #
    # Deliberately NOT using the "Max" column: it's the best price *per
    # outcome* across dozens of real bookmakers, not a price any single book
    # actually offers - storing it as if it were one bookmaker's line is
    # exactly the "mix-and-match best odds" bug this session already fixed
    # once for live data (it reappeared here via a different door and
    # inflated backtest ROI into the thousands of percent).
    sources = [
        ("Football-Data.co.uk Avg", "AvgCH", "AvgH", "AvgCD", "AvgD", "AvgCA", "AvgA"),
        ("Pinnacle (closing)", "PSCH", "PSH", "PSCD", "PSD", "PSCA", "PSA"),
    ]

    added = 0
    for bookmaker, hc, h, dc, d, ac, a in sources:
        home_odds = _get_float(row, hc, h)
        draw_odds = _get_float(row, dc, d)
        away_odds = _get_float(row, ac, a)
        if not (home_odds and draw_odds and away_odds):
            continue
        if not (home_odds > 1 and draw_odds > 1 and away_odds > 1):
            continue

        if Odds.query.filter_by(match_id=match.id, bookmaker=bookmaker).first():
            continue

        db.session.add(Odds(
            match_id=match.id, bookmaker=bookmaker,
            home_odds=home_odds, draw_odds=draw_odds, away_odds=away_odds,
        ))
        added += 1

    return added


def attach_historical_odds(seasons: list[str] | None = None) -> int:
    """Attach real historical odds (market average & best, plus Pinnacle
    closing where available) to matches we already have real results for
    (from football-data.org / API-Football), so /backtest can finally
    compute ROI instead of just accuracy.

    Free, no API key, no rate limit - football-data.co.uk publishes plain
    CSV files. `seasons` are their season codes like "2425", "2526"
    (defaults to the last two).
    """
    seasons = seasons or ["2425", "2526"]
    total = 0

    for league_name, code in BIG5_LEAGUE_CODES.items():
        for season in seasons:
            rows = _fetch_csv_rows(f"{BASE_URL}/mmz4281/{season}/{code}.csv")
            for row in rows:
                event_date = _parse_date(row.get("Date", ""), row.get("Time", ""))
                if not event_date:
                    continue
                try:
                    home_goals = int(row["FTHG"])
                    away_goals = int(row["FTAG"])
                except (KeyError, ValueError):
                    continue

                match = _find_existing_match(league_name, event_date, home_goals, away_goals)
                if match:
                    total += _attach_odds_row(match, row)

    rus_rows = _fetch_csv_rows(RUSSIA_CSV_URL)
    wanted_seasons = {"2024/2025", "2025/2026"}
    for row in rus_rows:
        if row.get("Season") not in wanted_seasons:
            continue
        event_date = _parse_date(row.get("Date", ""), row.get("Time", ""))
        if not event_date:
            continue
        try:
            home_goals = int(row["HG"])
            away_goals = int(row["AG"])
        except (KeyError, ValueError):
            continue

        match = _find_existing_match("Russian Premier League", event_date, home_goals, away_goals)
        if match:
            total += _attach_odds_row(match, row)

    if total:
        db.session.commit()

    return total
