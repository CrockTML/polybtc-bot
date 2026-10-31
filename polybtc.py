#!/usr/bin/env python3
"""Paper trading for Polymarket 'Bitcoin Up or Down' 5-minute markets.

Roles: Scout (price + fair odds), Trader (paper entries/settlement),
Risk (daily review). Paper only: no wallet, no order signing, no live path.

Usage:
  python3 polybtc.py paper [--minutes N]   run the paper loop
  python3 polybtc.py risk                  summarize the log
"""
import json, math, os, sys, time, urllib.request, statistics, argparse
import socket, threading; socket.setdefaulttimeout(10)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
RPC = "https://ethereum-rpc.publicnode.com"
CL_BTC_USD = "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"  # Chainlink BTC/USD, Ethereum mainnet
HERE = os.path.dirname(os.path.abspath(__file__))
TRADES = os.path.join(HERE, "trades.jsonl")
DECISIONS = os.path.join(HERE, "decisions.jsonl")
STATE = os.path.join(HERE, "state.json")

CFG = dict(
    window=300,          # seconds; 5m is the shortest BTC series on Polymarket
    # 2026-10-30 run 3: run 2 won 47.6% at 56.9c avg cost (model said 65%). Model fixed (full vol,
    # Chainlink stream = settlement source); old edge_max/min_fair filters dropped.
    # 2026-10-30 run 3b: calibration over 200 settled windows (10.5k snapshots) showed the market
    # price is well calibrated and the model is not (1-5c asks won 0.2% vs model 11%). Only
    # favorites at 70-80c won more often than their price. Model-free favorite rule, same bankroll.
    prob_shrink=0.0,     # p = ask + shrink*(model_fair - ask); 0 = trust market price only
    edge_min=-0.05,      # with shrink 0 edge = -(fee+slippage) ~ -2.6c; this just sanity-bounds it
    min_price=0.70,      # never buy below 70c (long-shots lost money in every bucket)
    max_price=0.80,      # never pay more than 80c
    max_secs_left=200,   # enter only in the last 200s of the window
    stake_max=3.0,       # cap any single bet at $3
    min_dist_sigma=0.0,  # BTC must be >=0.5 sigma of the remaining move away from the strike
    cl_max_age=30,       # skip if latest Chainlink tick is older than this (no fallback)
    start_balance=100.0,
    stake_frac=0.025,    # 2.5% of current balance
    stake_fixed=None,    # set a dollar amount to override the fraction
    min_order=1.0,       # Polymarket minimum order (~$1)
    fee_rate=0.07,       # from Gamma feeSchedule (taker): fee/share = rate*p*(1-p)
    slippage=0.01,
    poll=5,
    min_secs_left=20,    # don't enter in the last seconds
    min_secs_in=20,      # skip the first seconds while the strike settles
    stale_secs=60,       # skip trading if spot hasn't changed for this long
)

def get(url, data=None, timeout=8):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0",
                                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def log(path, rec):
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")

# ---------------- Scout ----------------
RTDS = "wss://ws-live-data.polymarket.com"
class CLFeed:
    """Chainlink BTC/USD Data Streams via Polymarket's live-data socket (the price Polymarket settles on)."""
    def __init__(self):
        self.pts = {}; self.last = None  # sec -> price; (sec, price)
        threading.Thread(target=self.run, daemon=True).start()
    def add(self, ts_ms, v):
        t = int(ts_ms) // 1000; self.pts[t] = float(v)
        if not self.last or t >= self.last[0]: self.last = (t, float(v))
        if len(self.pts) > 4000:
            for k in sorted(self.pts)[:-3600]: del self.pts[k]
    def run(self):
        import websocket
        while True:
            try:
                ws = websocket.create_connection(RTDS, timeout=15)
                ws.send(json.dumps({"action": "subscribe", "subscriptions": [{"topic": "crypto_prices_chainlink",
                        "type": "*", "filters": '{"symbol":"btc/usd"}'}]}))
                while True:
                    m = ws.recv()
                    if not m or not m.startswith("{"): continue
                    p = json.loads(m).get("payload") or {}
                    if "data" in p:
                        for d in p["data"]: self.add(d["timestamp"], d["value"])
                    elif "value" in p: self.add(p["timestamp"], p["value"])
            except Exception as e:
                print("chainlink feed error:", e, flush=True); time.sleep(3)
    def price(self):
        return self.last if self.last else (None, None)
    def at(self, t):
        for d in (0, 1, -1, 2):
            if t + d in self.pts: return self.pts[t + d]
        return None

def chainlink_price():
    try:
        r = get(RPC, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                "params": [{"to": CL_BTC_USD, "data": "0xfeaf968c"}, "latest"]}).encode())
        h = r["result"][2:]
        ans = int(h[64:128], 16) / 1e8
        upd = int(h[192:256], 16)
        return ans, upd
    except Exception:
        return None, None

def spot_price():
    for name, url, f in (
        # NOTE: api.coinbase.com/v2/prices/.../spot is cached and returned a frozen price; use the live exchange ticker.
        ("coinbase", "https://api.exchange.coinbase.com/products/BTC-USD/ticker", lambda j: float(j["price"])),
        ("binance", "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", lambda j: float(j["price"])),
        ("kraken", "https://api.kraken.com/0/public/Ticker?pair=XBTUSD", lambda j: float(list(j["result"].values())[0]["c"][0])),
    ):
        try:
            return f(get(url)), name
        except Exception:
            continue
    return None, None

def minute_open(ts):
    """Open price of the 1-minute candle at ts (Coinbase, then Binance)."""
    try:
        j = get(f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60&start={ts-120}&end={ts+180}")
        for c in j:
            if c[0] == ts: return float(c[3])
    except Exception: pass
    try:
        j = get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime={ts*1000}&limit=1")
        return float(j[0][1])
    except Exception: pass
    try:
        j = get(f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1&since={ts-60}")
        for c in list(j["result"].values())[0]:
            if int(c[0]) == ts: return float(c[1])
    except Exception: pass
    return None

class Scout:
    def __init__(self, feed):
        self.feed = feed; self.last_change = None; self.lastp = None
    def tick(self):
        t, p = self.feed.price()
        if p and p != self.lastp: self.last_change = time.time(); self.lastp = p
        return p, "chainlink"
    def age(self):
        t, p = self.feed.price()
        return (time.time() - t) if t else None
    def stale(self):
        a = self.age()
        if a is None or a > CFG["cl_max_age"]: return "chainlink_stale"
        if self.last_change and time.time() - self.last_change > CFG["stale_secs"]: return "price_frozen"
        return None
    @property
    def hist(self):
        now = int(time.time()); ks = [k for k in sorted(self.feed.pts) if k > now - 3600]
        return [(k, self.feed.pts[k]) for k in ks]
    def vol_per_sec(self):
        h = self.hist
        if len(h) < 12: return 0.6 / math.sqrt(365*86400)  # ~60% annualized default
        rets = [math.log(h[i][1]/h[i-1][1]) / math.sqrt(max(h[i][0]-h[i-1][0], 1)) for i in range(1, len(h))]
        return max(statistics.pstdev(rets), 1e-6)
    def sigma_left(self, secs_left):
        return self.vol_per_sec() * math.sqrt(max(secs_left, 1))
    def fair_up(self, price, strike, secs_left, window):
        """P(Chainlink price at window end >= strike), driftless lognormal, full remaining vol."""
        s = self.sigma_left(secs_left)
        z = math.log(price / strike) / s
        return 0.5 * (1 + math.erf(z / math.sqrt(2)))

# ---------------- Market discovery ----------------
def current_market(window):
    start = int(time.time()) // window * window
    slug = f"btc-updown-{window//60}m-{start}"
    ev = get(f"{GAMMA}/events?slug={slug}")
    if not ev: return None
    m = ev[0]["markets"][0]
    toks = json.loads(m["clobTokenIds"]); outs = json.loads(m["outcomes"])
    return dict(slug=slug, start=start, end=start+window, question=m["question"],
                tokens=dict(zip(outs, toks)), fee=(m.get("feeSchedule") or {}).get("rate", CFG["fee_rate"]))

def best_ask(token):
    b = get(f"{CLOB}/book?token_id={token}")
    asks = b.get("asks") or []
    if not asks: return None, 0
    a = min(asks, key=lambda x: float(x["price"]))
    return float(a["price"]), float(a["size"])

def resolved(slug):
    try:
        m = get(f"{GAMMA}/events?slug={slug}")[0]["markets"][0]
        px = json.loads(m.get("outcomePrices") or "[]")
        if m.get("closed") and px:
            outs = json.loads(m["outcomes"])
            for o, p in zip(outs, px):
                if float(p) >= 0.99: return o
    except Exception: pass
    return None

# ---------------- Trader (paper) ----------------
def load_state():
    if os.path.exists(STATE): return json.load(open(STATE))
    return {"balance": CFG["start_balance"], "open": {}}

def save_state(s): json.dump(s, open(STATE, "w"), indent=1)

def stake_for(bal):
    st = CFG["stake_fixed"] or bal * CFG["stake_frac"]
    st = min(max(st, CFG["min_order"]), CFG.get("stake_max") or st)
    return round(min(st, bal), 2)

def settle(state):
    now = time.time()
    for slug, pos in list(state["open"].items()):
        if now < pos["end"] + 30: continue
        win = resolved(slug); how = "polymarket"
        if win is None and now > pos["end"] + 600:
            # fallback: spot at end vs strike (not the official TWAP)
            e = minute_open(pos["end"] - 60)
            if e and pos.get("strike"):
                win = "Up" if e >= pos["strike"] else "Down"; how = "spot_fallback"
        if win is None: continue
        payout = pos["shares"] if win == pos["side"] else 0.0
        pnl = payout - pos["cost"]
        state["balance"] = round(state["balance"] + payout, 4)
        rec = dict(pos, type="settle", winner=win, how=how, payout=round(payout, 4),
                   pnl=round(pnl, 4), balance=state["balance"], ts=int(now))
        log(TRADES, rec); del state["open"][slug]
        print(f"SETTLE {slug} {pos['side']} winner={win} pnl={pnl:+.2f} bal={state['balance']:.2f}")
    save_state(state)

def paper(minutes):
    feed = CLFeed(); sc = Scout(feed); state = load_state(); w = CFG["window"]
    strikes = {}; stop = time.time() + minutes*60 if minutes else None
    print(f"Paper mode. Balance ${state['balance']:.2f}")
    while not stop or time.time() < stop:
        try:
            settle(state)
            price, src = sc.tick()
            m = current_market(w)
            if not m or not price:
                time.sleep(CFG["poll"]); continue
            # Strike = official opening price of the window, captured once. The 1m candle for the
            # window start isn't published in the first seconds, so retry until it is; never
            # overwrite it with the current price.
            if strikes.get(m["slug"]) is None:
                strikes = {m["slug"]: feed.at(m["start"])}  # Chainlink price at window open = price to beat
            strike = strikes[m["slug"]]
            strike_ok = strike is not None
            if not strike_ok: strike = price  # display only; trading blocked below
            left = m["end"] - time.time()
            fu = sc.fair_up(price, strike, left, w)
            cl_age = sc.age(); sig = sc.sigma_left(left); dist = abs(math.log(price/strike)) / sig
            rec = dict(ts=int(time.time()), slug=m["slug"], src=src, price=price, strike=strike,
                       chainlink=price, chainlink_age=round(cl_age, 1) if cl_age is not None else None,
                       dist_sigma=round(dist, 3),
                       secs_left=round(left), fair_up=round(fu, 4), balance=state["balance"])
            for side, fair in (("Up", fu), ("Down", 1-fu)):
                ask, sz = best_ask(m["tokens"][side])
                if ask is None: continue
                cost = ask + m["fee"]*ask*(1-ask) + CFG["slippage"]
                p_adj = ask + CFG.get("prob_shrink", 1.0) * (fair - ask)
                edge = p_adj - cost
                rec[side] = dict(ask=ask, size=sz, fair=round(fair, 4), edge=round(edge, 4))
                reason = None
                if m["slug"] in state["open"]: reason = "position_open"
                elif state["balance"] < CFG["min_order"]: reason = "bankroll_empty"
                elif left < CFG["min_secs_left"]: reason = "too_late"
                elif w - left < CFG["min_secs_in"]: reason = "too_early"
                elif left > CFG.get("max_secs_left", 1e9): reason = "too_early_in_window"
                elif ask > CFG["max_price"]: reason = "ask_above_cap"
                elif ask < CFG.get("min_price", 0): reason = "ask_below_floor"
                elif edge < CFG["edge_min"]: reason = "edge_too_small"
                elif not strike_ok: reason = "no_strike"
                elif sc.stale(): reason = sc.stale()
                elif dist < CFG["min_dist_sigma"]: reason = "too_close_to_strike"
                if reason is None:
                    stake = stake_for(state["balance"])
                    shares = round(stake / cost, 4)
                    state["balance"] = round(state["balance"] - stake, 4)
                    pos = dict(type="entry", ts=int(time.time()), slug=m["slug"], end=m["end"], side=side,
                               ask=ask, cost_per_share=round(cost, 4), fair=round(fair, 4), edge=round(edge, 4),
                               shares=shares, cost=stake, strike=strike, price=price, dist_sigma=round(dist, 3), balance=state["balance"])
                    state["open"][m["slug"]] = pos; log(TRADES, pos); save_state(state)
                    print(f"ENTER {side} {m['slug']} ask={ask} fair={fair:.3f} edge={edge:+.3f} ${stake} bal={state['balance']:.2f}")
                    rec[side]["action"] = "ENTER"
                else:
                    rec[side]["action"] = reason
            log(DECISIONS, rec)
            print(f"{time.strftime('%H:%M:%S')} {m['slug']} {src} {price:.0f} vs {strike:.0f} "
                  f"left={left:.0f}s fairUp={fu:.3f} Up={rec.get('Up',{}).get('ask')} Down={rec.get('Down',{}).get('ask')} bal={state['balance']:.2f}", flush=True)
            if state["balance"] < CFG["min_order"] and not state["open"]:
                print("Bankroll exhausted; stopping."); break
        except Exception as e:
            print("error:", e, flush=True)
        time.sleep(CFG["poll"])
    settle(state)

# ---------------- Risk ----------------
def risk():
    if not os.path.exists(TRADES): print("No trades yet."); return
    recs = [json.loads(l) for l in open(TRADES)]
    ent = [r for r in recs if r["type"] == "entry"]; st = [r for r in recs if r["type"] == "settle"]
    st_state = load_state()
    print(f"Entries {len(ent)}  settled {len(st)}  open {len(st_state['open'])}")
    print(f"Start ${CFG['start_balance']:.2f}  cash now ${st_state['balance']:.2f}")
    if st:
        pnl = [r["pnl"] for r in st]; wins = sum(p > 0 for p in pnl)
        eq, peak, mdd = CFG["start_balance"], CFG["start_balance"], 0
        print("Balance over time (after each settlement):")
        for r in st:
            eq += r["pnl"]; peak = max(peak, eq); mdd = max(mdd, peak - eq)
            print(f"  {time.strftime('%m-%d %H:%M', time.localtime(r['ts']))} {r['side']:4} {r['pnl']:+7.2f} -> ${eq:.2f}")
        print(f"Total P&L {sum(pnl):+.2f}  win rate {wins/len(st):.0%}  avg edge {statistics.mean(r['edge'] for r in ent):.3f}  max drawdown ${mdd:.2f}")
        flags = []
        if sum(pnl) < 0: flags.append("negative P&L")
        if len(st) < 30: flags.append("sample too small (<30 settled) to trust")
        if any(r["how"] == "spot_fallback" for r in st): flags.append("some settled by spot fallback, not official result")
        if mdd > 0.3*CFG["start_balance"]: flags.append("drawdown over 30% of bankroll")
        if st_state["balance"] < CFG["min_order"]: flags.append("bankroll exhausted")
        print("FLAGS: " + ("; ".join(flags) if flags else "none"))


# ---------------- Dashboard ----------------
def _jsonl(path, n=None):
    if not os.path.exists(path): return []
    lines = open(path).readlines()
    return [json.loads(l) for l in (lines[-n:] if n else lines)]

def api_state():
    st = load_state(); dec = _jsonl(DECISIONS, 400); tr = _jsonl(TRADES)
    settles = [r for r in tr if r["type"] == "settle"]
    eq = [{"ts": 0, "bal": CFG["start_balance"]}]; e = CFG["start_balance"]
    for r in settles: e += r["pnl"]; eq.append({"ts": r["ts"], "bal": round(e, 2)})
    day0 = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    today = [r for r in settles if r["ts"] >= day0]
    wins = sum(r["pnl"] > 0 for r in settles)
    return dict(now=int(time.time()), start=CFG["start_balance"], cash=st["balance"],
        open=list(st["open"].values()), equity=eq,
        today_pnl=round(sum(r["pnl"] for r in today), 2), total_pnl=round(sum(r["pnl"] for r in settles), 2),
        win_rate=(wins/len(settles)) if settles else None, settled=len(settles),
        last=dec[-1] if dec else None,
        prices=[{"ts": d["ts"], "p": d["price"], "cl": d.get("chainlink")} for d in dec[-120:]],
        decisions=dec[-25:][::-1])

def api_trades(): return _jsonl(TRADES)[::-1]

PAGE = r"""<!doctype html><html><head><meta charset=utf-8><title>polybtc</title><style>
body{background:#0b0e11;color:#d1d4dc;font:13px Menlo,monospace;margin:16px}h1{font-size:16px;color:#f0b90b}
.g{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.c{background:#151a21;border:1px solid #232a33;padding:10px;border-radius:6px}
.k{color:#7a8594;font-size:11px}.v{font-size:20px}.up{color:#0ecb81}.dn{color:#f6465d}
table{width:100%;border-collapse:collapse}td,th{padding:3px 6px;border-bottom:1px solid #232a33;text-align:left}
svg{width:100%;height:120px}.w2{grid-column:span 2}.w4{grid-column:span 4}</style></head><body>
<h1>BTC Up/Down 5m &middot; PAPER <span class=k id=ts></span></h1><div class=g>
<div class=c><div class=k>BTC (spot / Chainlink ref)</div><div class=v id=px></div><div class=k id=cl></div></div>
<div class=c><div class=k>Balance (start $25)</div><div class=v id=bal></div><div class=k id=pnl></div></div>
<div class=c><div class=k>Market</div><div id=mk></div></div>
<div class=c><div class=k>Fair P(Up) vs asks</div><div id=fv></div></div>
<div class="c w2"><div class=k>BTC price</div><svg id=ch></svg></div>
<div class="c w2"><div class=k>Equity curve</div><svg id=eq></svg></div>
<div class="c w4"><div class=k>Open position</div><div id=op></div></div>
<div class="c w2"><div class=k>Closed trades</div><table id=tt></table></div>
<div class="c w2"><div class=k>Decision log</div><table id=dl></table></div></div>
<script>
const DATA=null;
function line(id,ys){const s=document.getElementById(id),w=600,h=120;if(ys.length<2){s.innerHTML='';return}
const mn=Math.min(...ys),mx=Math.max(...ys),r=(mx-mn)||1;s.setAttribute('viewBox',`0 0 ${w} ${h}`);s.setAttribute('preserveAspectRatio','none');
s.innerHTML=`<polyline fill=none stroke=${ys.at(-1)>=ys[0]?'#0ecb81':'#f6465d'} stroke-width=2 points="${ys.map((y,i)=>`${i*w/(ys.length-1)},${h-5-(y-mn)/r*(h-10)}`).join(' ')}"/>`}
const $=i=>document.getElementById(i),f=x=>x==null?'-':x,m=x=>(x>=0?'<span class=up>+':'<span class=dn>')+x.toFixed(2)+'</span>';
function render(s,t){const d=s.last||{};$('ts').textContent=new Date(s.now*1000).toLocaleTimeString();
$('px').textContent=d.price?'$'+d.price.toLocaleString():'-';$('cl').textContent='Chainlink '+f(d.chainlink)+' ('+f(d.chainlink_age)+'s old) · strike '+f(d.strike&&d.strike.toFixed(2));
$('bal').textContent='$'+(s.cash+s.open.reduce((a,p)=>a+p.cost,0)).toFixed(2);
$('pnl').innerHTML='today '+m(s.today_pnl)+' · total '+m(s.total_pnl)+' · win '+(s.win_rate==null?'-':Math.round(s.win_rate*100)+'%')+' ('+s.settled+')';
$('mk').innerHTML=(d.slug||'-')+'<br>'+f(d.secs_left)+'s left';
const U=d.Up||{},D=d.Down||{};$('fv').innerHTML=`fair Up <b>${f(d.fair_up)}</b><br><span class=up>Up</span> ask ${f(U.ask)} edge ${f(U.edge)} ${f(U.action)}<br><span class=dn>Down</span> ask ${f(D.ask)} edge ${f(D.edge)} ${f(D.action)}`;
line('ch',s.prices.map(p=>p.p));line('eq',s.equity.map(e=>e.bal));
$('op').innerHTML=s.open.length?s.open.map(p=>`${p.slug} ${p.side} @${p.ask} $${p.cost} fair ${p.fair} edge ${p.edge}`).join('<br>'):'none';
$('tt').innerHTML='<tr><th>time<th>market<th>side<th>ask<th>P&L<th>bal'+t.filter(r=>r.type=='settle').map(r=>`<tr><td>${new Date(r.ts*1000).toLocaleTimeString()}<td>${r.slug.slice(-10)}<td>${r.side}<td>${r.ask}<td>${m(r.pnl)}<td>${r.balance.toFixed(2)}`).join('');
$('dl').innerHTML='<tr><th>time<th>left<th>fair<th>Up<th>Down'+s.decisions.map(r=>`<tr><td>${new Date(r.ts*1000).toLocaleTimeString()}<td>${r.secs_left}<td>${r.fair_up}<td>${f((r.Up||{}).ask)} ${f((r.Up||{}).action)}<td>${f((r.Down||{}).ask)} ${f((r.Down||{}).action)}`).join('')}
async function tick(){try{const[s,t]=await Promise.all([fetch('/api/state').then(r=>r.json()),fetch('/api/trades').then(r=>r.json())]);render(s,t)}catch(e){}}
if(DATA)render(DATA.s,DATA.t);else{tick();setInterval(tick,2000)}
</script></body></html>"""

def dashboard(port):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path.startswith("/api/state"): body, ct = json.dumps(api_state()), "application/json"
            elif self.path.startswith("/api/trades"): body, ct = json.dumps(api_trades()), "application/json"
            else: body, ct = PAGE, "text/html"
            b = body.encode(); self.send_response(200); self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    print(f"Dashboard on http://localhost:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()

def snapshot():
    data = json.dumps({"s": api_state(), "t": api_trades()})
    out = os.path.join(HERE, "snapshot.html")
    open(out, "w").write(PAGE.replace("const DATA=null;", "const DATA=" + data + ";"))
    print("Wrote", out)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("mode", choices=["paper", "risk", "dashboard", "snapshot"])
    ap.add_argument("--minutes", type=float, default=0)
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--stake", type=float); ap.add_argument("--frac", type=float)
    a = ap.parse_args()
    if a.stake: CFG["stake_fixed"] = a.stake
    if a.frac: CFG["stake_frac"] = a.frac
    ap2 = a.mode
    if ap2 == "paper": paper(a.minutes)
    elif ap2 == "risk": risk()
    elif ap2 == "dashboard": dashboard(a.port)
    else: snapshot()
