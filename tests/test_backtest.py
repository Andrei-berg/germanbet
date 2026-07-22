import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from app import create_app, db
from app.config import Config
from app.models import Team, Match
from app.analytics.backtest import _load_match_data
from app.analytics.elo import elo_to_probability


@pytest.fixture()
def app_ctx():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    class TestConfig(Config):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{path}"

    app = create_app(TestConfig)
    with app.app_context():
        yield
    os.remove(path)


def test_backtest_ignores_present_day_elo_rating(app_ctx):
    now = datetime.now(timezone.utc)

    # Team.elo_rating holds today's rating, updated using matches that may
    # have happened *after* the one below. It must not leak into the replay.
    home = Team(name="Home FC", elo_rating=2200.0)
    away = Team(name="Away FC", elo_rating=800.0)
    db.session.add_all([home, away])
    db.session.flush()

    match = Match(
        home_team_id=home.id, away_team_id=away.id,
        match_date=now - timedelta(days=30),
        status="finished", home_goals=1, away_goals=1,
    )
    db.session.add(match)
    db.session.commit()

    data = _load_match_data()
    assert len(data) == 1
    home_p, draw_p, away_p = data[0]["elo"]

    # A neutral 1500-vs-1500 prior plus home advantage gives a modest edge,
    # not the blowout implied by the stored (present-day) 2200 vs 800 rating.
    assert 0.4 < home_p < 0.7
    assert away_p > 0.1


def test_backtest_updates_state_only_after_scoring_each_match(app_ctx):
    now = datetime.now(timezone.utc)

    home = Team(name="Home FC")
    away = Team(name="Away FC")
    db.session.add_all([home, away])
    db.session.flush()

    earlier = Match(
        home_team_id=home.id, away_team_id=away.id,
        match_date=now - timedelta(days=60),
        status="finished", home_goals=3, away_goals=0,
    )
    later = Match(
        home_team_id=away.id, away_team_id=home.id,
        match_date=now - timedelta(days=10),
        status="finished", home_goals=0, away_goals=0,
    )
    db.session.add_all([earlier, later])
    db.session.commit()

    data = _load_match_data()
    assert len(data) == 2

    # The first (chronologically earliest) match must be scored from the
    # neutral 1500/1500 prior, not from ratings updated by the later match.
    first_home_p, _, first_away_p = data[0]["elo"]
    assert abs(first_home_p - data[0]["elo"][0]) < 1e-9
    assert first_home_p > first_away_p  # only home advantage differentiates them

    # In match 2 the original (stronger, post-win) home team plays away
    # against a fresh opponent. Its win probability should beat what a
    # perfectly neutral 1500-vs-1500 away side would get - proving the
    # match 1 result actually carried over into match 2's state, rather
    # than every match being scored from a reset 1500/1500 prior.
    _, _, neutral_away_baseline = elo_to_probability(1500.0, 1500.0)
    _, _, second_away_p = data[1]["elo"]
    assert second_away_p > neutral_away_baseline
