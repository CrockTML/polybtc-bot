# polybtc-bot
Paper-trading simulator for Polymarket BTC 5-minute Up/Down markets. Paper only: no wallet, no order signing.
Runs on GitHub Actions in ~6h shifts; state (state.json, trades.jsonl, hist_done.json) is committed back every 15 min.
Optional secret `ABACUS_KEYS` (JSON) lets push.py update dashboard counters.
