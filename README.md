# polybtc-bot
Paper-trading simulator for Polymarket BTC 5-minute Up/Down markets. Paper only: no wallet, no order signing.
Runs on GitHub Actions in ~6h shifts; state (state.json, trades.jsonl, hist_done.json) is committed back every 15 min.
Optional secret `ABACUS_KEYS` (JSON) lets push.py update dashboard counters.

## Run 3 (2026-10-30, fresh $100)
Prices from the Chainlink BTC/USD stream Polymarket settles on (skip if >30s old, no fallback); strike = Chainlink price at window open.
Fair odds use the full remaining volatility. Trades only when BTC is >=0.5 sigma from the strike, ask <=55c, edge >=8c after fees; stake 2.5%.
Run 2 archived on the box; dashboard history keys continue at index 85 (push.py OFF).
