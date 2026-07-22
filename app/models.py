from datetime import datetime, timezone
from app import db


class Team(db.Model):
    __tablename__ = "teams"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    country = db.Column(db.String(100), default="")
    league = db.Column(db.String(100), default="")

    elo_rating = db.Column(db.Float, default=1500.0)

    home_goals_for = db.Column(db.Float, default=0.0)
    home_goals_against = db.Column(db.Float, default=0.0)
    away_goals_for = db.Column(db.Float, default=0.0)
    away_goals_against = db.Column(db.Float, default=0.0)
    matches_played = db.Column(db.Integer, default=0)

    external_id = db.Column(db.String(100), unique=True, nullable=True)
    football_data_id = db.Column(db.String(50), unique=True, nullable=True)
    api_football_id = db.Column(db.String(50), unique=True, nullable=True)

    home_matches = db.relationship(
        "Match", foreign_keys="Match.home_team_id", back_populates="home_team"
    )
    away_matches = db.relationship(
        "Match", foreign_keys="Match.away_team_id", back_populates="away_team"
    )

    def update_stats(self):
        home = [m for m in self.home_matches if m.home_goals is not None]
        away = [m for m in self.away_matches if m.away_goals is not None]

        if home:
            self.home_goals_for = sum(m.home_goals for m in home) / len(home)
            self.home_goals_against = sum(m.away_goals for m in home) / len(home)
        if away:
            self.away_goals_for = sum(m.away_goals for m in away) / len(away)
            self.away_goals_against = sum(m.home_goals for m in away) / len(away)

        self.matches_played = len(home) + len(away)


class Match(db.Model):
    __tablename__ = "matches"

    id = db.Column(db.Integer, primary_key=True)
    home_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    away_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    league = db.Column(db.String(100), default="")
    match_date = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default="scheduled")

    home_goals = db.Column(db.Integer, nullable=True)
    away_goals = db.Column(db.Integer, nullable=True)

    home_team = db.relationship("Team", foreign_keys=[home_team_id], back_populates="home_matches")
    away_team = db.relationship("Team", foreign_keys=[away_team_id], back_populates="away_matches")

    odds_list = db.relationship("Odds", back_populates="match", cascade="all, delete-orphan")
    predictions = db.relationship("Prediction", back_populates="match", cascade="all, delete-orphan")

    external_id = db.Column(db.String(100), unique=True, nullable=True)

    def result(self):
        if self.home_goals is None:
            return None
        if self.home_goals > self.away_goals:
            return "H"
        if self.home_goals == self.away_goals:
            return "D"
        return "A"

    def best_prediction(self):
        return Prediction.query.filter_by(match_id=self.id).order_by(Prediction.ev.desc()).first()


class Odds(db.Model):
    __tablename__ = "odds"

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("matches.id"), nullable=False)
    bookmaker = db.Column(db.String(100), nullable=False)
    home_odds = db.Column(db.Float, nullable=False)
    draw_odds = db.Column(db.Float, nullable=False)
    away_odds = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    match = db.relationship("Match", back_populates="odds_list")


class Prediction(db.Model):
    __tablename__ = "predictions"

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("matches.id"), nullable=False)

    home_prob = db.Column(db.Float, nullable=False)
    draw_prob = db.Column(db.Float, nullable=False)
    away_prob = db.Column(db.Float, nullable=False)

    home_fair_odds = db.Column(db.Float, nullable=False)
    draw_fair_odds = db.Column(db.Float, nullable=False)
    away_fair_odds = db.Column(db.Float, nullable=False)

    ev_home = db.Column(db.Float, nullable=True)
    ev_draw = db.Column(db.Float, nullable=True)
    ev_away = db.Column(db.Float, nullable=True)

    ev = db.Column(db.Float, nullable=True)
    kelly_pct = db.Column(db.Float, nullable=True)
    bet_pct = db.Column(db.Float, nullable=True)
    bet_amount = db.Column(db.Float, nullable=True)

    verdict = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    match = db.relationship("Match", back_populates="predictions")


class Bet(db.Model):
    __tablename__ = "bets"

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("matches.id"), nullable=False)
    outcome = db.Column(db.String(10), nullable=False)
    odds = db.Column(db.Float, nullable=False)
    stake = db.Column(db.Float, nullable=False)
    result = db.Column(db.String(20), default="pending")
    profit = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    match = db.relationship("Match")


class BankrollLog(db.Model):
    __tablename__ = "bankroll_log"

    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    change = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Setting(db.Model):
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.String(500), nullable=False)

    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        entry = cls.query.filter_by(key=key).first()
        return entry.value if entry else default

    @classmethod
    def set(cls, key: str, value: str) -> None:
        entry = cls.query.filter_by(key=key).first()
        if entry:
            entry.value = value
        else:
            entry = cls(key=key, value=value)
            db.session.add(entry)
        db.session.commit()

    @classmethod
    def get_float(cls, key: str, default: float = 0.0) -> float:
        try:
            return float(cls.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    @classmethod
    def get_int(cls, key: str, default: int = 0) -> int:
        try:
            return int(cls.get(key, str(default)))
        except (ValueError, TypeError):
            return default
