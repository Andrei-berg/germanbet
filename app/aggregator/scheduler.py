from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from app.config import Config

scheduler = BackgroundScheduler()


def start_scheduler(app) -> None:
    if scheduler.running:
        return

    from app.aggregator.odds_client import sync_odds_from_api, sync_results_from_odds_api
    from app.aggregator.thesportsdb_client import backfill_team_history
    from app.value_finder.value_evaluator import evaluate_all_upcoming

    def update_all():
        with app.app_context():
            synced = sync_results_from_odds_api()
            if synced:
                print(f"Синхронизировано завершённых матчей: {synced}")
            sync_odds_from_api()
            backfill_team_history()
            evaluate_all_upcoming()

    interval = max(Config.UPDATE_INTERVAL_MINUTES, 5)
    scheduler.add_job(update_all, "interval", minutes=interval, id="data_sync")
    scheduler.start()
