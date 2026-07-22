import os
import secrets
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Falls back to a fresh random key each process start (not a fixed
    # hardcoded default) so sessions/CSRF tokens can't be forged by anyone
    # who's read the source. Set SECRET_KEY in the environment for a stable
    # key across restarts.
    SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)

    SPORTSDB_API_KEY = os.getenv("SPORTSDB_API_KEY", "")
    ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
    FOOTBALL_DATA_API_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "")
    API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")

    DEFAULT_BANK = 100_000.0
    KELLY_FRACTION = 0.25
    MAX_BET_PCT = 0.03
    MIN_EV = 0.02

    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "germanbet.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPDATE_INTERVAL_MINUTES = 30
