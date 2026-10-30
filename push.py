#!/usr/bin/env python3
"""Pushes paper-bot counters to Abacus for the dashboard. Keys come from env ABACUS_KEYS (JSON), never from the repo."""
import json,os,sys,time,urllib.request,urllib.error
import socket; socket.setdefaulttimeout(10)
HERE=os.path.dirname(os.path.abspath(__file__))
NS="polybtc-crocker-r2x7";M0=1793300000//60
RAW=os.environ.get("ABACUS_KEYS","").strip()
if not RAW: print("ABACUS_KEYS not set; push disabled",flush=True); sys.exit(0)
K=json.loads(RAW)
LASTV={}
ERRS=[]
def put(k,v,force=False):
  v=int(v)
  if not force and LASTV.get(k)==v: return
  for att in range(3):
    try:
      r=urllib.request.Request(f"https://abacus.jasoncameron.dev/set/{NS}/{k}?value={v}",method="POST",headers={"Authorization":"Bearer "+K[k],"User-Agent":"Mozilla/5.0 polybtc"});urllib.request.urlopen(r,timeout=10).read();LASTV[k]=v;return
    except urllib.error.HTTPError as e:
      body=e.read()[:120].decode("utf-8","replace")
      ERRS.append(f"{time.strftime('%H:%M:%S')} set {k} HTTP {e.code} {body}");del ERRS[:-40]
      if e.code==429 or e.code>=500: time.sleep(3*(att+1)); continue
      raise
    except Exception as e:
      ERRS.append(f"{time.strftime('%H:%M:%S')} set {k} {e!r}");del ERRS[:-40];time.sleep(2)
  raise RuntimeError("put failed "+k)
def getv(k):
  try: return int(json.load(urllib.request.urlopen(urllib.request.Request(f"https://abacus.jasoncameron.dev/get/{NS}/{k}",headers={"User-Agent":"Mozilla/5.0 polybtc"}),timeout=10))["value"])
  except Exception: return 0
HF=os.path.join(HERE,"hist_done.json")
HD=set(json.load(open(HF))) if os.path.exists(HF) else set()
def mk(k,v):
  try: urllib.request.urlopen(urllib.request.Request(f"https://abacus.jasoncameron.dev/create/{NS}/{k}?initializer={int(v)}",headers={"User-Agent":"Mozilla/5.0 polybtc"}),timeout=10).read()
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
    a,b=enc(t)
    try: mk(f"h{i:04d}",a);mk(f"g{i:04d}",b)
    except Exception as e: print("hist err",i,repr(e),flush=True); return
    HD.add(i);json.dump(sorted(HD),open(HF,"w"))
def step(name,f):
  try: f(); return True
  except Exception as e: print("err",name,repr(e),flush=True); return False
sent=None;pp=getv("pptr");lastmin=0;lag=0
while True:
  d=None
  try: d=json.load(urllib.request.urlopen("http://localhost:8787/api/state",timeout=10))
  except Exception as e: print("err state",repr(e),flush=True)
  try: L=[json.loads(x) for x in open(os.path.join(HERE,"trades.jsonl")) if x.strip()]
  except Exception as e: print("err trades",repr(e),flush=True); L=None
  if L is not None:
    def ring():
      global sent
      start=max(0,len(L)-30) if sent is None else sent
      for i in range(start,len(L)):
        a,b=enc(L[i]);put(f"t{i%30:02d}",a);put(f"a{i%30:02d}",b);sent=i+1
      put("tptr",len(L),force=True)
    step("ring",ring)
    step("hist",lambda:hist(L))
    def selfcheck():
      global lag,sent
      r=getv("tptr")
      if r<len(L) or len(HD)<len(L):
        lag+=1
        if lag>=2:
          print(f"!!! DASHBOARD LAG: tptr={r} hist={len(HD)} trades={len(L)} -> catching up",flush=True)
          sent=max(0,min(sent or 0,r,len(L)-30) if r<len(L) else sent)
      else: lag=0
    step("selfcheck",selfcheck)
  if d:
    def counters():
      global oc,px
      oc=sum(o.get("cost",0) for o in d["open"]);settled=d["settled"];px=(d.get("last") or {}).get("price",0)
      put("cash",round(d["cash"]*100));put("open",round(oc*100));put("pnl",round((d["cash"]+oc-d["start"])*100)+100000)
      put("settled",settled);put("wins",round((d.get("win_rate") or 0)*settled));put("price",round(px));put("upd",int(time.time()))
    px=0;step("counters",counters)
    def chart():
      global pp,lastmin
      mi=int(time.time()//60)-M0
      if px and mi!=lastmin:
        put(f"p{pp%60:02d}",mi*2**24+round(px));pp+=1;put("pptr",pp);lastmin=mi
    step("chart",chart)
  print("pass",time.strftime("%H:%M:%S"),"trades",len(L) if L else "?","hist",len(HD),"sent",sent,flush=True)
  try: open(os.path.join(HERE,"push_status.txt"),"w").write(f"pass {time.strftime('%FT%TZ',time.gmtime())} trades {len(L) if L else '?'} sent {sent} hist {len(HD)}\n"+"\n".join(ERRS)+"\n")
  except Exception: pass
  time.sleep(30)
