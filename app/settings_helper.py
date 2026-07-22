from __future__ import annotations

from app.models import Setting
from app.config import Config


def load_settings() -> dict:
    return {
        "default_bank": Setting.get_float("default_bank", Config.DEFAULT_BANK),
        "kelly_fraction": Setting.get_float("kelly_fraction", Config.KELLY_FRACTION),
        "max_bet_pct": Setting.get_float("max_bet_pct", Config.MAX_BET_PCT),
        "min_ev": Setting.get_float("min_ev", Config.MIN_EV),
        "update_interval": Setting.get_int("update_interval", Config.UPDATE_INTERVAL_MINUTES),
        "poisson_weight": Setting.get_float("poisson_weight", 0.50),
        "elo_weight": Setting.get_float("elo_weight", 0.30),
        "form_weight": Setting.get_float("form_weight", 0.20),
    }


def save_setting(key: str, value: object) -> None:
    Setting.set(key, str(value))
