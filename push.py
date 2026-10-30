#!/usr/bin/env python3
"""Pushes paper-bot counters to Abacus for the dashboard. Keys come from env ABACUS_KEYS (JSON), never from the repo."""
import json,os,sys,time,urllib.request,urllib.error
import socket; socket.setdefaulttimeout(10)
HERE=os.path.dirname(os.path.abspath(__file__))
NS="polybtc-crocker-9f3k";M0=1793300000//60
RAW=os.environ.get("ABACUS_KEYS","").strip()
if not RAW: print("ABACUS_KEYS not set; push disabled",flush=True); sys.exit(0)
K=json.loads(RAW)
def put(k,v):
  r=urllib.request.Request(f"https://abacus.jasoncameron.dev/set/{NS}/{k}?value={int(v)}",method="POST",headers={"Authorization":"Bearer "+K[k]});urllib.request.urlopen(r,timeout=10).read()
def getv(k):
  try: return int(json.load(urllib.request.urlopen(f"https://abacus.jasoncameron.dev/get/{NS}/{k}",timeout=10))["value"])
  except Exception: return 0
HF=os.path.join(HERE,"hist_done.json")
HD=set(json.load(open(HF))) if os.path.exists(HF) else set()
def mk(k,v):
  try: urllib.request.urlopen(f"https://abacus.jasoncameron.dev/create/{NS}/{k}?initializer={int(v)}",timeout=10).read()
  except urllib.error.HTTPError as e:
    if e.code!=409: raise
def enc(t):
  kind=(0 if t["side"]=="Up" else 1) if t["type"]=="entry" else (2 if t.get("pnl",0)>0 else 3)
  amt=t["cost"] if t["type"]=="entry" else t.get("pnl",0)
  return ((int(t["ts"]//60)-M0)*2**26+kind*2**24+round(t.get("price",0)),
          (0 if t["side"]=="Up" else 1)*2**21+round(amt*100)+100000+(int(t["ts"])%60)*2**22)
def hist(L):
  for i,t in enumerate(L):
    if i in HD: continue
    a,b=enc(t);mk(f"h{i:04d}",a);mk(f"g{i:04d}",b)
    HD.add(i);json.dump(sorted(HD),open(HF,"w"))
sent=None;pp=getv("pptr");lastmin=0
while True:
  try:
    d=json.load(urllib.request.urlopen("http://localhost:8787/api/state",timeout=10))
    oc=sum(o.get("cost",0) for o in d["open"]);settled=d["settled"];px=(d.get("last") or {}).get("price",0)
    put("cash",round(d["cash"]*100));put("open",round(oc*100));put("pnl",round((d["cash"]+oc-d["start"])*100)+100000)
    put("settled",settled);put("wins",round(d["win_rate"]*settled));put("price",round(px));put("upd",int(time.time()))
    mi=int(time.time()//60)-M0
    if px and mi!=lastmin:
      put(f"p{pp%60:02d}",mi*2**24+round(px));pp+=1;put("pptr",pp);lastmin=mi
    L=[json.loads(x) for x in open(os.path.join(HERE,"trades.jsonl")) if x.strip()]
    start=max(0,len(L)-30) if sent is None else sent
    for i in range(start,len(L)):
      a,b=enc(L[i]);put(f"t{i%30:02d}",a);put(f"a{i%30:02d}",b)
    hist(L);sent=len(L);put("tptr",sent);print("ok",time.strftime("%H:%M:%S"),flush=True)
  except Exception as e: print("err",e,flush=True)
  time.sleep(30)
