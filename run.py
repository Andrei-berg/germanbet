#!/usr/bin/env python3
import os
import sys
from app import create_app, db
from app.value_finder.value_evaluator import evaluate_all_upcoming

app = create_app()


def init_db():
    with app.app_context():
        db.create_all()
        print("БД инициализирована.")


def sync_real_data():
    with app.app_context():
        from app.aggregator.odds_client import sync_odds_from_api, sync_results_from_odds_api
        from app.aggregator.thesportsdb_client import backfill_team_history

        synced = sync_results_from_odds_api()
        if synced:
            print(f"Синхронизировано завершённых матчей: {synced}")

        added = sync_odds_from_api()
        print(f"Загружено новых коэффициентов: {added}")

        warmed = backfill_team_history()
        if warmed:
            print(f"Догружена история по новым командам: {warmed} матчей")


def run_analysis():
    with app.app_context():
        count = evaluate_all_upcoming()
        print(f"Проанализировано матчей с коэффициентами: {count}")


def backfill_odds(max_requests: int = 50):
    """Manual, budget-capped: attaches real historical bookmaker odds to
    already-known finished matches, so /backtest can compute ROI instead of
    just accuracy. Requires a paid the-odds-api plan - not run automatically
    because each call costs ~10x a live odds request. Re-run to keep working
    through the backlog; usage: `python run.py backfill-odds [max_requests]`.
    """
    with app.app_context():
        from app.aggregator.odds_client import backfill_historical_odds
        added = backfill_historical_odds(max_requests=max_requests)
        print(f"Добавлено исторических коэффициентов: {added} (за {max_requests} запросов максимум)")


def start_app():
    init_db()
    sync_real_data()
    run_analysis()
    from app.aggregator.scheduler import start_scheduler
    start_scheduler(app)
    print(f"Авто-обновление: каждые {app.config.get('UPDATE_INTERVAL_MINUTES', 30)} мин")

    # Defaults to localhost-only: the Werkzeug debugger allows arbitrary code
    # execution, so it must never be reachable from outside this machine
    # unless you deliberately opt in (e.g. to reach it from your phone on the
    # same LAN) by setting HOST=0.0.0.0 yourself.
    host = os.getenv("HOST", "127.0.0.1")
    debug = os.getenv("FLASK_DEBUG", "true").lower() not in ("0", "false", "no")
    app.run(debug=debug, host=host, port=8080)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "init-db":
            init_db()
        elif cmd == "sync":
            sync_real_data()
        elif cmd == "analyze":
            run_analysis()
        elif cmd == "backfill-odds":
            max_requests = int(sys.argv[2]) if len(sys.argv) > 2 else 50
            backfill_odds(max_requests=max_requests)
        elif cmd == "serve":
            start_app()
        else:
            print(f"Неизвестная команда: {cmd}")
            print("Доступные команды: init-db, sync, analyze, backfill-odds, serve")
            sys.exit(1)
    else:
        start_app()
