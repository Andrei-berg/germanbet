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
        from app.aggregator.api_football_client import sync_upcoming_odds, LEAGUE_IDS

        synced = sync_results_from_odds_api()
        if synced:
            print(f"Синхронизировано завершённых матчей: {synced}")

        added = sync_odds_from_api()
        print(f"Загружено новых коэффициентов: {added}")

        warmed = backfill_team_history()
        if warmed:
            print(f"Догружена история по новым командам: {warmed} матчей")

        af_odds = sync_upcoming_odds(leagues=LEAGUE_IDS)
        if af_odds:
            print(f"Загружено коэффициентов (API-Football, все лиги): {af_odds}")


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


def backfill_rpl(seasons: list[int]):
    """Manual: pulls RPL (Russian Premier League) season history via
    API-Football - the only source that covers this league at all
    (football-data.org's free tier doesn't). Worth running for several past
    seasons while the paid API-Football plan is active, since a downgrade to
    its free tier likely restricts how far back historical fixtures go.
    Usage: `python run.py backfill-rpl 2025 2024 2023`.
    """
    with app.app_context():
        from app.aggregator.api_football_client import backfill_season_results
        for season in seasons:
            added = backfill_season_results(season)
            print(f"РПЛ {season}: добавлено матчей {added}")


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
        elif cmd == "backfill-rpl":
            seasons = [int(s) for s in sys.argv[2:]] if len(sys.argv) > 2 else [2025]
            backfill_rpl(seasons)
        elif cmd == "serve":
            start_app()
        else:
            print(f"Неизвестная команда: {cmd}")
            print("Доступные команды: init-db, sync, analyze, backfill-odds, backfill-rpl, serve")
            sys.exit(1)
    else:
        start_app()
