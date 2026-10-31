# polybtc-bot
Paper-trading simulator for Polymarket BTC 5-minute Up/Down markets. Paper only: no wallet, no order signing.
Runs on GitHub Actions in ~6h shifts; state (state.json, trades.jsonl, hist_done.json) is committed back every 15 min.
Optional secret `ABACUS_KEYS` (JSON) lets push.py update dashboard counters.

## Run 3 (2026-10-30, fresh $100)
Prices from the Chainlink BTC/USD stream Polymarket settles on (skip if >30s old, no fallback); strike = Chainlink price at window open.
Fair odds use the full remaining volatility. Trades only when BTC is >=0.5 sigma from the strike, ask <=55c, edge >=8c after fees; stake 2.5%.
Run 2 archived on the box; dashboard history keys continue at index 85 (push.py OFF).

## Run 4 (2026-10-30 23:22 NT, $100) - ended $80.02
Favorite at 70-80c in the last 200s, no model check. Won 73% but break-even after fees was ~77%. Archived on the box.

## Run 5 (2026-10-31, fresh $100) - current rules
Buy one side per window only when ALL hold: 20-120s left, ask 60-95c, BTC >=0.3 sigma from the strike,
and modeled edge (model fair - ask - fee - 1c slippage) >= 0. Stake 2.5% of balance, max $3.
Backtest (317 settled windows, time split 65/35): in-sample 9 bets 9 won +41.7% ROI; out-of-sample 7 bets 6 won +21.6% ROI.
Very small sample; expect ~5% of windows to trade. Shut off if it is losing after ~50 bets.
