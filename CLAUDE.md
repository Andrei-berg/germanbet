# GermanBet

Value-betting tool for football: aggregates odds/results, models true probabilities (Elo + Poisson + form), and flags positive-EV bets sized with fractional Kelly.

## Stack
- Python 3.12, Flask 3.1, Flask-SQLAlchemy 3.1, SQLite (`germanbet.db`)
- Bootstrap-Flask templates (Jinja2), Flask-WTF (CSRF)
- APScheduler for periodic odds/result sync
- pandas / numpy / scipy for analytics (Elo, Poisson, backtest)
- pytest for tests; no linter configured

## Commands
```
source venv/bin/activate && pip install -r requirements.txt   # setup
python run.py                 # dev server (init-db + sync + analyze + serve, port 8080)
python run.py serve            # same, explicit
python run.py init-db          # create tables only
python run.py sync             # pull odds/results without serving
python run.py analyze          # re-run value evaluation only
python run.py backfill-odds N  # paid-tier historical odds backfill, budget-capped
pytest                         # run test suite
```
There is no build step (no bundler/compiler) and no lint command configured.

## Critical Rules

**Testing**: `pytest` must pass before committing. Tests live in `tests/`, one file per module under test (`test_elo.py`, `test_kelly.py`, `test_poisson.py`, `test_backtest.py`).

**Database**: SQLite at `germanbet.db`, gitignored — never commit it. Schema changes go through `db.create_all()` in `app/__init__.py`; `migrations/` exists but is currently unused (empty). A match may only be marked `finished` with a real score via `app/aggregator/settlement.py::finish_match` — it is the single place that updates Elo, team stats, and settles bets together. Never set `Match.status = "finished"` anywhere else.

**Git Workflow**: Commit only after tests pass. Don't commit `.env`, `*.db`, or `venv/`. Standard branch is `main`.

**Code Style**: Match existing patterns — dataclasses for value objects (see `odds_selector.py`), module-level functions over classes for stateless logic (analytics, bankroll), docstrings only where a decision needs justification (see `settlement.py`, `odds_selector.py`, `constants.py` for the style).

**What NOT to Do**:
- Don't compare the model against a single bookmaker's odds — always use the median consensus (`pick_reference_odds`).
- Don't lower `MIN_MATCHES_FOR_BET` without re-validating; below it, Elo/goal stats are noise, not signal.
- Don't bind the dev server to `0.0.0.0` by default — the Werkzeug debugger allows arbitrary code execution.
- Don't hardcode `SECRET_KEY` — it must come from the environment or fall back to a random per-process value.

## Key Files
- `run.py` — CLI entrypoints (init-db, sync, analyze, backfill-odds, serve) and scheduler bootstrap
- `app/__init__.py` — Flask app factory, extension init, template filters
- `app/config.py` — env-driven config, betting defaults (Kelly fraction, max bet %, min EV)
- `app/constants.py` — tunable thresholds with the empirical reasoning behind each value
- `app/models.py` — SQLAlchemy models: Team, Match, Odds, Prediction, Bet, BankrollLog, Setting
- `app/value_finder/value_evaluator.py` — core pipeline: probabilities → fair odds → EV → verdict
- `app/aggregator/settlement.py` — the only path that finalizes a match result
- `app/aggregator/odds_selector.py` — consensus (median) reference-odds logic
- `app/bankroll/kelly.py` — fractional Kelly sizing with stop-loss cap

## IMPORTANT
- Preserve existing code and inline reasoning comments (e.g. in `constants.py`, `odds_selector.py`, `settlement.py`) — they encode empirically-learned constraints, not guesses. Don't remove or "simplify" them without understanding why they're there.
- Don't assume external API/account state (API keys, quota tier, DB contents). Ask or check `.env` / `Setting` table rather than assuming defaults are populated.

## Advisor: Project Invariants
Consult these before changing related code — each one encodes a past failure mode, not a style preference:
1. **Reference odds are always the cross-bookmaker median**, never a single book's line — comparing against one bookmaker previously produced fake 25-97% "value" bets (`odds_selector.py`).
2. **`MIN_MATCHES_FOR_BET = 5`** — below this, Elo/goal-average stats are flat defaults, not signal; lowering it re-introduces false value bets (`constants.py`).
3. **`MAX_TRUSTED_EV_PCT = 25.0`** — EV above this is flagged "ПОДОЗРИТЕЛЬНО" instead of recommended; real market edges are rarely double digits, so a higher figure means a model bug, not a discovery.
4. **Match results only change through `settlement.finish_match`** — it's the sole path that keeps Elo, team stats, and bet settlement in sync; writing `match.status`/`home_goals`/`away_goals` elsewhere will desync them.
5. **`SECRET_KEY` has no fixed fallback** — it's a fresh random value per process start unless set in the environment, by design, so sessions can't be forged from source access alone.

## Advisor Consultation Rules (for executor)
- Before modifying `value_finder/`, `bankroll/`, `aggregator/settlement.py`, `aggregator/odds_selector.py`, or `constants.py`, re-read the invariant list above — a change that looks like a simplification may silently reintroduce a fixed bug.
- If a task requires violating one of these invariants (e.g. lowering `MIN_MATCHES_FOR_BET`, reading a single bookmaker instead of consensus), stop and confirm with the user before proceeding — don't treat it as a routine refactor.
- If new project-specific constraints are discovered during work (another "confirmed in practice" case), add them here rather than only fixing the symptom.
