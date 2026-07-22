LEAGUE_AVG_HOME = 1.53
LEAGUE_AVG_AWAY = 1.15

# Below this many real finished matches, a team's Elo/goal-average stats are
# still mostly flat/noisy defaults - any "value" the model finds against them
# is model noise, not a real edge (confirmed in practice: at threshold=3,
# Man City were priced as underdogs at home to Bournemouth - obviously wrong).
# TheSportsDB's free tier caps backfill at ~5 events/team for half the
# leagues, so this will show few or no bets until real results accumulate via
# odds_client.sync_results_from_odds_api() - that's the honest trade-off.
MIN_MATCHES_FOR_BET = 5

# Above this EV%, treat the "value" as more likely a model error than a real
# edge, and flag it instead of recommending a bet. Real, sustained edges
# against efficient football markets are usually low single digits, rarely
# above ~15-20% even for well-resourced professional models. An EV this high
# from a model calibrated on a few dozen matches almost always means the
# model's probability estimate is wrong, not that the market is.
MAX_TRUSTED_EV_PCT = 25.0
