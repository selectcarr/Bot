#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse, hashlib, json, os, re, sqlite3, tempfile, unittest
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

ENGINE_VERSION = "1.0.0"
LOOKBACK_DAYS = 20
MIN_SAMPLES = 3
MIN_DEAL_PERCENT = 1.0
MAX_DEAL_PERCENT = 15.0
ELIGIBLE_ACTIONS = {"scheduled", "live", "dry_run"}

SYSTEM_RUNTIME = Path(os.getenv("ACCURATE_RUNTIME_DIR", "accurate_average_runtime"))
SYSTEM_DB = SYSTEM_RUNTIME / "accurate_average.db"
OBS_DB = SYSTEM_RUNTIME / "listing_observations.sqlite3"
MARKET_RUNTIME = Path(os.getenv("SELECT_CARR_MARKET_RUNTIME_DIR", "select_carr_market_runtime"))
MARKET_DB = MARKET_RUNTIME / "select_carr_market.sqlite3"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ALLOW_SEND = os.getenv("SELECT_CARR_MARKET_ALLOW_SEND", "true").strip().lower() in {"1","true","yes","on"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_listings(
 source_key TEXT PRIMARY KEY, source TEXT NOT NULL, source_ad_id TEXT NOT NULL,
 url TEXT NOT NULL, title TEXT NOT NULL, brand TEXT NOT NULL, model TEXT NOT NULL,
 trim TEXT NOT NULL, model_year INTEGER NOT NULL, condition TEXT NOT NULL,
 body_condition TEXT NOT NULL, mileage INTEGER, price INTEGER NOT NULL CHECK(price>0),
 group_key TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_group_window ON market_listings(group_key,last_seen);

CREATE TABLE IF NOT EXISTS market_price_history(
 id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL,
 price INTEGER NOT NULL CHECK(price>0), observed_at TEXT NOT NULL,
 system_execution_id INTEGER,
 UNIQUE(source_key,price,observed_at)
);

CREATE TABLE IF NOT EXISTS market_deals(
 event_key TEXT PRIMARY KEY, source_key TEXT NOT NULL, source TEXT NOT NULL,
 source_ad_id TEXT NOT NULL, url TEXT NOT NULL, brand TEXT NOT NULL,
 model TEXT NOT NULL, trim TEXT NOT NULL, model_year INTEGER NOT NULL,
 condition TEXT NOT NULL, body_condition TEXT NOT NULL, mileage INTEGER,
 candidate_price INTEGER NOT NULL, average_price INTEGER NOT NULL,
 discount_percent REAL NOT NULL, sample_count INTEGER NOT NULL,
 system_execution_id INTEGER NOT NULL, candidate_seen_at TEXT NOT NULL,
 status TEXT NOT NULL, queued_at TEXT NOT NULL, sent_at TEXT,
 telegram_message_id INTEGER, last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_market_deals_status ON market_deals(status,queued_at);

CREATE TABLE IF NOT EXISTS market_state(
 key TEXT PRIMARY KEY, value TEXT NOT NULL
);
"""

def now_utc(): return datetime.now(timezone.utc)
def iso(dt: datetime): return dt.astimezone(timezone.utc).isoformat()

def parse_dt(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except Exception:
        return None

def norm(value):
    t=str(value or "").strip().lower()
    for a,b in [("\u200c"," "),("\u200f"," "),("\u200e"," "),("ي","ی"),("ك","ک"),("ۀ","ه"),("ة","ه")]:
        t=t.replace(a,b)
    t=re.sub(r"[_/\\|,:;؛،!?؟()\[\]{}]+"," ",t)
    return re.sub(r"\s+"," ",t).strip()

def group_key(row):
    return "|".join((norm(row.get("brand")),norm(row.get("model")),norm(row.get("trim")),str(int(row.get("model_year") or 0))))

def signature(row):
    return (norm(row.get("brand")),norm(row.get("model")),norm(row.get("trim")),
            int(row.get("model_year") or 0),str(row.get("condition") or ""),
            str(row.get("body_condition") or ""),row.get("mileage"),int(row.get("price") or 0))

def canon_url(v):
    s=str(v or "").strip()
    return s.rstrip("/") if len(s)>8 else s

def event_key(source_key,price):
    return hashlib.sha256(f"{source_key}|{int(price)}".encode()).hexdigest()

@contextmanager
def db(path:Path, readonly=False):
    if readonly:
        c=sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True,timeout=30)
        c.execute("PRAGMA query_only=ON")
    else:
        path.parent.mkdir(parents=True,exist_ok=True)
        c=sqlite3.connect(path,timeout=30)
        c.execute("PRAGMA journal_mode=WAL")
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    try:
        yield c
        if not readonly: c.commit()
    except:
        if not readonly: c.rollback()
        raise
    finally:
        c.close()

def init_market(path=MARKET_DB):
    with db(path) as c: c.executescript(SCHEMA)

def sget(c,key):
    r=c.execute("SELECT value FROM market_state WHERE key=?",(key,)).fetchone()
    return str(r["value"]) if r else None

def sset(c,key,val):
    c.execute("INSERT INTO market_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(val)))

def from_system(row):
    d=dict(row)
    return dict(source_key=str(d["source_key"]),source=str(d["source"]),source_ad_id=str(d["source_ad_id"]),
                url=canon_url(d.get("url")),title=str(d.get("title") or "")[:300],brand=str(d["brand"]),
                model=str(d["model"]),trim=str(d.get("trim") or ""),model_year=int(d["model_year"]),
                condition=str(d.get("condition") or "unknown"),body_condition=str(d.get("body_condition") or "unknown"),
                mileage=None if d.get("mileage") is None else int(d["mileage"]),price=int(d["price"]),
                first_seen=str(d.get("first_seen") or d.get("last_seen")),last_seen=str(d.get("last_seen") or d.get("first_seen")))

def from_observation(row):
    d=dict(row)
    if str(d.get("outcome"))!="accepted": return None
    try: p=json.loads(str(d.get("parsed_fields_json") or "{}"))
    except: return None
    if not all(p.get(k) not in (None,"") for k in ("brand","model","model_year","price")): return None
    if type(p["price"]) is not int or p["price"]<=0 or type(p["model_year"]) is not int: return None
    return dict(source_key=str(d["source_key"]),source=str(d["source"]),source_ad_id=str(d["source_ad_id"]),
                url=canon_url(d.get("url")),title=str(d.get("title") or "")[:300],brand=str(p["brand"]),model=str(p["model"]),
                trim=str(p.get("trim") or ""),model_year=int(p["model_year"]),condition=str(p.get("condition") or "unknown"),
                body_condition=str(p.get("body_condition") or "unknown"),mileage=None if p.get("mileage") is None else int(p["mileage"]),
                price=int(p["price"]),first_seen=str(d.get("first_seen") or d.get("last_seen")),last_seen=str(d.get("last_seen") or d.get("first_seen")))

def current(c,key):
    r=c.execute("SELECT * FROM market_listings WHERE source_key=?",(key,)).fetchone()
    return dict(r) if r else None

def upsert(c,row,execution_id=None):
    old=current(c,row["source_key"]); g=group_key(row); stamp=iso(now_utc())
    is_new=old is None
    changed=is_new or signature(old)!=signature(row)
    if old is None:
        c.execute("""INSERT INTO market_listings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (row["source_key"],row["source"],row["source_ad_id"],row["url"],row["title"],row["brand"],row["model"],row["trim"],
                   row["model_year"],row["condition"],row["body_condition"],row["mileage"],row["price"],g,row["first_seen"],row["last_seen"],stamp))
        c.execute("INSERT OR IGNORE INTO market_price_history(source_key,price,observed_at,system_execution_id) VALUES(?,?,?,?)",
                  (row["source_key"],row["price"],row["last_seen"],execution_id))
    else:
        if int(old["price"])!=int(row["price"]):
            c.execute("INSERT OR IGNORE INTO market_price_history(source_key,price,observed_at,system_execution_id) VALUES(?,?,?,?)",
                      (row["source_key"],row["price"],row["last_seen"],execution_id))
        c.execute("""UPDATE market_listings SET source=?,source_ad_id=?,url=?,title=?,brand=?,model=?,trim=?,model_year=?,
                     condition=?,body_condition=?,mileage=?,price=?,group_key=?,last_seen=?,updated_at=? WHERE source_key=?""",
                  (row["source"],row["source_ad_id"],row["url"],row["title"],row["brand"],row["model"],row["trim"],row["model_year"],
                   row["condition"],row["body_condition"],row["mileage"],row["price"],g,row["last_seen"],stamp,row["source_key"]))
    return is_new,changed

def bootstrap(mc,system_db,obs_db):
    n1=n2=0
    if system_db.is_file():
        with db(system_db,True) as c:
            for r in c.execute("SELECT * FROM aa_listings ORDER BY last_seen,source_key"):
                upsert(mc,from_system(r)); n1+=1
    if obs_db.is_file():
        with db(obs_db,True) as c:
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "listing_observations" in tables:
                for r in c.execute("SELECT * FROM listing_observations WHERE outcome='accepted' ORDER BY last_seen,source_key"):
                    x=from_observation(r)
                    if x: upsert(mc,x); n2+=1
    sset(mc,"bootstrapped","1"); sset(mc,"bootstrap_at",iso(now_utc()))
    return {"system_rows":n1,"observation_rows":n2}

@dataclass(frozen=True)
class Execution:
    id:int; started_at:str; finished_at:str; action:str; accepted:int

def latest_execution(system_db):
    with db(system_db,True) as c:
        r=c.execute("""SELECT id,started_at,finished_at,action,accepted FROM aa_execution_history
                       WHERE finished_at IS NOT NULL AND action IN ('scheduled','live','dry_run')
                       ORDER BY id DESC LIMIT 1""").fetchone()
    return None if not r else Execution(int(r["id"]),str(r["started_at"]),str(r["finished_at"]),str(r["action"]),int(r["accepted"] or 0))

def executions_after(system_db,last_id):
    with db(system_db,True) as c:
        rows=c.execute("""SELECT id,started_at,finished_at,action,accepted FROM aa_execution_history
                          WHERE id>? AND finished_at IS NOT NULL ORDER BY id""",(last_id,)).fetchall()
    return [Execution(int(r["id"]),str(r["started_at"]),str(r["finished_at"]),str(r["action"]),int(r["accepted"] or 0))
            for r in rows if str(r["action"]) in ELIGIBLE_ACTIONS]

def observations_for_execution(obs_db,e):
    if not obs_db.is_file(): return []
    with db(obs_db,True) as c:
        rows=c.execute("""SELECT * FROM listing_observations WHERE outcome='accepted'
                          AND julianday(last_seen)>=julianday(?) AND julianday(last_seen)<=julianday(?)
                          ORDER BY last_seen,source_key""",(e.started_at,e.finished_at)).fetchall()
    out=[]
    for r in rows:
        x=from_observation(r)
        if x: out.append(x)
    return out

def dedupe_refs(rows):
    out=[]; keys=set(); urls=set()
    for r in sorted(rows,key=lambda x:(str(x["last_seen"]),str(x["source_key"])),reverse=True):
        k=str(r["source_key"]); u=canon_url(r.get("url"))
        if k in keys or (u and u in urls): continue
        keys.add(k)
        if u: urls.add(u)
        out.append(r)
    return out

def refs_for(mc,candidate,as_of):
    cutoff=as_of-timedelta(days=LOOKBACK_DAYS)
    rows=[dict(r) for r in mc.execute("""SELECT * FROM market_listings WHERE group_key=? AND source_key<>? AND price>0
              AND julianday(last_seen)>=julianday(?) AND julianday(last_seen)<=julianday(?)
              ORDER BY last_seen DESC,source_key""",(group_key(candidate),candidate["source_key"],iso(cutoff),iso(as_of)))]
    return dedupe_refs(rows)

@dataclass(frozen=True)
class Evaluation:
    status:str; sample_count:int; average_price:int|None; discount_percent:float|None

def evaluate(candidate,refs):
    prices=[int(r["price"]) for r in refs if int(r.get("price") or 0)>0]
    if len(prices)<MIN_SAMPLES: return Evaluation("insufficient_samples",len(prices),None,None)
    avg=sum(prices)/len(prices)
    if avg<=0: return Evaluation("invalid_reference",len(prices),None,None)
    price=int(candidate["price"]); disc=(avg-price)/avg*100
    if price>=avg: st="not_below_reference"
    elif disc<MIN_DEAL_PERCENT: st="discount_below_minimum"
    elif disc>MAX_DEAL_PERCENT: st="suspicious_low"
    else: st="deal"
    return Evaluation(st,len(prices),round(avg),round(disc,2))

def system_sent_same_price(system_db,source_key,price):
    with db(system_db,True) as c:
        r=c.execute("SELECT 1 FROM aa_sent_deals WHERE source_key=? AND price=? LIMIT 1",(source_key,int(price))).fetchone()
    return r is not None

def queue_deal(mc,candidate,e,execution_id):
    key=event_key(candidate["source_key"],candidate["price"])
    if mc.execute("SELECT 1 FROM market_deals WHERE event_key=?",(key,)).fetchone(): return False
    mc.execute("""INSERT INTO market_deals(event_key,source_key,source,source_ad_id,url,brand,model,trim,model_year,condition,body_condition,
                  mileage,candidate_price,average_price,discount_percent,sample_count,system_execution_id,candidate_seen_at,status,queued_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (key,candidate["source_key"],candidate["source"],candidate["source_ad_id"],candidate["url"],candidate["brand"],candidate["model"],
                candidate["trim"],candidate["model_year"],candidate["condition"],candidate["body_condition"],candidate["mileage"],candidate["price"],
                e.average_price,e.discount_percent,e.sample_count,execution_id,candidate["last_seen"],"pending",iso(now_utc())))
    return True

def suppress_if_legacy(mc,system_db,candidate):
    if not system_sent_same_price(system_db,candidate["source_key"],candidate["price"]): return False
    mc.execute("UPDATE market_deals SET status='suppressed_system_b',sent_at=COALESCE(sent_at,?) WHERE event_key=? AND status='pending'",
               (iso(now_utc()),event_key(candidate["source_key"],candidate["price"])))
    return True

def format_message(r):
    trim=f" {r['trim']}" if r.get("trim") else ""
    mileage="نامشخص" if r.get("mileage") is None else f"{int(r['mileage']):,} کیلومتر"
    condition="صفر" if r.get("condition")=="zero" else "کارکرده"
    body={"factory":"کارخانه / صفر","clean":"بدون رنگ","minor_paint":"لکه رنگ","painted":"رنگ‌شده",
          "full_paint":"تمام/دور رنگ","replaced":"قطعه تعویضی","accident":"تصادفی/آسیب‌دیده","unknown":"نامشخص"}.get(str(r.get("body_condition")),str(r.get("body_condition") or "نامشخص"))
    return (f"🚗 دیل Select Carr\n\n🚘 خودرو:\n{r['brand']} {r['model']}{trim}\n\n📅 مدل:\n{r['model_year']}\n\n"
            f"🚦 وضعیت:\n{condition}\n\n🎨 وضعیت بدنه:\n{body}\n\n🛣 کارکرد:\n{mileage}\n\n"
            f"💰 قیمت:\n{int(r['candidate_price']):,} تومان\n\n📊 میانگین {int(r['sample_count'])} نمونه ۲۰ روزه:\n"
            f"{int(r['average_price']):,} تومان\n\n📉 زیر میانگین:\n{float(r['discount_percent']):.2f}٪\n\n"
            f"🔎 منبع:\n{r['source']}\n\n🔗 لینک:\n{r['url']}")

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: raise RuntimeError("Telegram secrets missing")
    data=json.dumps({"chat_id":TELEGRAM_CHAT_ID,"text":text,"disable_web_page_preview":True},ensure_ascii=False).encode()
    req=Request(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",data=data,headers={"Content-Type":"application/json"},method="POST")
    try:
        with urlopen(req,timeout=30) as resp:
            body=resp.read(); status=resp.status
    except (HTTPError,URLError,TimeoutError) as exc:
        raise RuntimeError(type(exc).__name__) from exc
    if status!=200: raise RuntimeError(f"HTTP{status}")
    d=json.loads(body.decode())
    if d.get("ok") is not True or not isinstance(d.get("result",{}).get("message_id"),int): raise RuntimeError("bad Telegram response")
    return d["result"]["message_id"]

def pending(mc):
    return [dict(r) for r in mc.execute("SELECT * FROM market_deals WHERE status='pending' ORDER BY system_execution_id,discount_percent DESC,candidate_price,event_key")]

def send_all(mc,allow_send):
    sent=failed=0
    for r in pending(mc):
        if not allow_send:
            print("\n--- SELECT CARR MARKET DRY RUN ---\n"+format_message(r)+"\n--- END ---")
            continue
        try: mid=send_telegram(format_message(r))
        except Exception as exc:
            failed+=1
            mc.execute("UPDATE market_deals SET last_error=? WHERE event_key=?",(type(exc).__name__,r["event_key"]))
            continue
        mc.execute("UPDATE market_deals SET status='sent',sent_at=?,telegram_message_id=?,last_error=NULL WHERE event_key=?",(iso(now_utc()),mid,r["event_key"]))
        sent+=1
    return sent,failed

def process_execution(mc,system_db,obs_db,e):
    obs=observations_for_execution(obs_db,e)
    if len(obs)<e.accepted:
        raise RuntimeError(f"candidate_feed_incomplete execution={e.id} accepted={e.accepted} retained_accepted={len(obs)}")
    changed=[]
    for row in obs:
        _,ch=upsert(mc,row,e.id)
        if ch: changed.append(row)
    as_of=parse_dt(e.finished_at)
    if as_of is None: raise RuntimeError("invalid finished_at")
    stats={"execution_id":e.id,"accepted":e.accepted,"changed_candidates":len(changed),"evaluated":0,"queued":0,"suspicious":0,"insufficient":0}
    for cand in changed:
        ev=evaluate(cand,refs_for(mc,cand,as_of))
        stats["evaluated"]+=1
        if ev.status=="deal":
            if queue_deal(mc,cand,ev,e.id): stats["queued"]+=1
            suppress_if_legacy(mc,system_db,cand)
        elif ev.status=="suspicious_low": stats["suspicious"]+=1
        elif ev.status=="insufficient_samples": stats["insufficient"]+=1
    sset(mc,"last_processed_execution_id",e.id); sset(mc,"last_processed_action",e.action); sset(mc,"last_processed_at",iso(now_utc()))
    return stats

def run_engine(system_db=SYSTEM_DB,obs_db=OBS_DB,market_db=MARKET_DB,allow_send=ALLOW_SEND):
    if not system_db.is_file(): raise RuntimeError("System B database missing")
    if not obs_db.is_file(): raise RuntimeError("System B observation database missing")
    init_market(market_db)
    with db(market_db) as mc:
        first=sget(mc,"bootstrapped")!="1"
        if first:
            print("BOOTSTRAP",json.dumps(bootstrap(mc,system_db,obs_db),ensure_ascii=False,sort_keys=True))
            latest=latest_execution(system_db)
            executions=[] if latest is None else [latest]
        else:
            last=int(sget(mc,"last_processed_execution_id") or 0)
            executions=executions_after(system_db,last)
        reports=[]
        for e in executions:
            rep=process_execution(mc,system_db,obs_db,e); reports.append(rep)
            print("EXECUTION",json.dumps(rep,ensure_ascii=False,sort_keys=True))
        for d in pending(mc):
            if system_sent_same_price(system_db,d["source_key"],d["candidate_price"]):
                mc.execute("UPDATE market_deals SET status='suppressed_system_b',sent_at=COALESCE(sent_at,?) WHERE event_key=?",(iso(now_utc()),d["event_key"]))
        send_allowed=allow_send and bool(executions) and all(e.action in {"scheduled","live"} for e in executions)
        sent,failed=send_all(mc,send_allowed)
        counts={str(r["status"]):int(r["n"]) for r in mc.execute("SELECT status,COUNT(*) n FROM market_deals GROUP BY status")}
        print("SUMMARY",json.dumps({"engine_version":ENGINE_VERSION,"lookback_days":LOOKBACK_DAYS,"min_samples":MIN_SAMPLES,
              "min_deal_percent":MIN_DEAL_PERCENT,"max_deal_percent":MAX_DEAL_PERCENT,"executions_processed":len(reports),
              "market_listings":mc.execute("SELECT COUNT(*) FROM market_listings").fetchone()[0],"deal_status_counts":counts,
              "messages_sent_this_run":sent,"message_failures_this_run":failed},ensure_ascii=False,sort_keys=True))
        return 1 if failed else 0

# ---------------- tests ----------------
def fx(key,price,*,source="divar",when=None,brand="Peugeot",model="207",trim="اتوماتیک",year=1403,condition="used",body="clean",mileage=50000,url=None):
    t=iso(when or now_utc())
    return dict(source_key=f"{source}|{key}",source=source,source_ad_id=key,url=url or f"https://x/{source}/{key}",title=key,
                brand=brand,model=model,trim=trim,model_year=year,condition=condition,body_condition=body,mileage=mileage,price=price,first_seen=t,last_seen=t)

class Tests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory(); self.r=Path(self.t.name); self.m=self.r/"m.db"; self.s=self.r/"s.db"; self.o=self.r/"o.db"; init_market(self.m)
        with sqlite3.connect(self.s) as c:
            c.executescript("""CREATE TABLE aa_listings(source_key TEXT PRIMARY KEY,source TEXT,source_ad_id TEXT,url TEXT,title TEXT,brand TEXT,model TEXT,trim TEXT,model_year INTEGER,condition TEXT,body_condition TEXT,raw_text TEXT,mileage INTEGER,price INTEGER,comparison_key TEXT,fingerprint TEXT,first_seen TEXT,last_seen TEXT,updated_at TEXT);
            CREATE TABLE aa_sent_deals(source_key TEXT PRIMARY KEY,source TEXT,source_ad_id TEXT,price INTEGER,average_price INTEGER,discount_percent REAL,sent_at TEXT);
            CREATE TABLE aa_execution_history(id INTEGER PRIMARY KEY,started_at TEXT,finished_at TEXT,action TEXT,sources TEXT,fetched INTEGER,accepted INTEGER,rejected INTEGER,duplicates INTEGER,deals_found INTEGER,messages_sent INTEGER,status TEXT,error_message TEXT);""")
        with sqlite3.connect(self.o) as c:
            c.execute("""CREATE TABLE listing_observations(source_key TEXT PRIMARY KEY,source TEXT,source_ad_id TEXT,url TEXT,title TEXT,received_text TEXT,text_sha256 TEXT,text_chars INTEGER,text_truncated INTEGER,received_scope TEXT,outcome TEXT,reason TEXT,claimed_body_in_text TEXT,parsed_fields_json TEXT,first_seen TEXT,last_seen TEXT)""")
    def tearDown(self): self.t.cleanup()
    def put(self,*rows):
        with db(self.m) as c:
            for r in rows: upsert(c,r)
    def test_01_min3(self): self.assertEqual(evaluate(fx("c",900),[fx("a",1000),fx("b",1000)]).status,"insufficient_samples")
    def test_02_one_percent(self): self.assertEqual(evaluate(fx("c",990),[fx(str(i),1000) for i in range(3)]).status,"deal")
    def test_03_fifteen_percent(self): self.assertEqual(evaluate(fx("c",850),[fx(str(i),1000) for i in range(3)]).status,"deal")
    def test_04_below_min(self): self.assertEqual(evaluate(fx("c",995),[fx(str(i),1000) for i in range(3)]).status,"discount_below_minimum")
    def test_05_over_max(self): self.assertEqual(evaluate(fx("c",840),[fx(str(i),1000) for i in range(3)]).status,"suspicious_low")
    def test_06_no_10_cap(self): self.assertEqual(evaluate(fx("c",990),[fx(str(i),1000) for i in range(37)]).sample_count,37)
    def test_07_group4(self): self.assertEqual(group_key(fx("a",1,condition="zero",body="factory")),group_key(fx("b",1,condition="used",body="accident")))
    def test_08_window(self):
        n=datetime(2026,9,26,tzinfo=timezone.utc); c=fx("c",900,when=n); ins=[fx(str(i),1000,when=n-timedelta(days=20)) for i in range(3)]; out=fx("z",1000,when=n-timedelta(days=20,seconds=1))
        self.put(c,*ins,out)
        with db(self.m) as x: refs=refs_for(x,c,n)
        self.assertEqual(len(refs),3)
    def test_09_self_excluded(self):
        n=now_utc(); c=fx("c",900,when=n); refs=[fx(str(i),1000,when=n) for i in range(3)]; self.put(c,*refs)
        with db(self.m) as x: got=refs_for(x,c,n)
        self.assertNotIn(c["source_key"],{r["source_key"] for r in got})
    def test_10_same_url_dedupe(self):
        n=now_utc(); c=fx("c",900,when=n); u="https://x/car/1"; a=fx("a",1000,source="bama",when=n,url=u); b=fx("b",1000,source="telegram",when=n,url=u); d=fx("d",1000,source="karnameh",when=n); self.put(c,a,b,d)
        with db(self.m) as x: got=refs_for(x,c,n)
        self.assertEqual(len(got),2)
    def test_11_unchanged_not_candidate(self):
        r=fx("x",1000)
        with db(self.m) as c:
            upsert(c,r,1); r2=dict(r); r2["last_seen"]=iso(now_utc()+timedelta(hours=1)); _,ch=upsert(c,r2,2)
        self.assertFalse(ch)
    def test_12_price_change_history(self):
        r=fx("x",1000)
        with db(self.m) as c:
            upsert(c,r,1); r2=dict(r); r2["price"]=950; r2["last_seen"]=iso(now_utc()+timedelta(hours=1)); _,ch=upsert(c,r2,2); prices=[q[0] for q in c.execute("SELECT price FROM market_price_history ORDER BY id")]
        self.assertTrue(ch); self.assertEqual(prices,[1000,950])
    def test_13_event_dedupe(self):
        e=Evaluation("deal",3,1000,10.0); r=fx("x",900)
        with db(self.m) as c: self.assertTrue(queue_deal(c,r,e,1)); self.assertFalse(queue_deal(c,r,e,2))
    def test_14_new_price_new_event(self):
        e=Evaluation("deal",3,1000,10.0); r=fx("x",900)
        with db(self.m) as c:
            queue_deal(c,r,e,1); r2=dict(r); r2["price"]=880; self.assertTrue(queue_deal(c,r2,Evaluation("deal",3,1000,12.0),2))
    def test_15_send_all_no_cap(self):
        e=Evaluation("deal",3,1000,10.0)
        with db(self.m) as c:
            for i in range(7): queue_deal(c,fx(str(i),900,source="telegram"),e,1)
            with patch(__name__+".send_telegram",side_effect=range(1,8)) as s: sent,fail=send_all(c,True)
        self.assertEqual((sent,fail,s.call_count),(7,0,7))
    def test_16_failure_pending(self):
        e=Evaluation("deal",3,1000,10.0)
        with db(self.m) as c:
            queue_deal(c,fx("x",900),e,1)
            with patch(__name__+".send_telegram",side_effect=RuntimeError("x")): sent,fail=send_all(c,True)
            st=c.execute("SELECT status FROM market_deals").fetchone()[0]
        self.assertEqual((sent,fail,st),(0,1,"pending"))
    def test_17_legacy_suppression_exact_price(self):
        r=fx("x",900); e=Evaluation("deal",3,1000,10.0)
        with sqlite3.connect(self.s) as c: c.execute("INSERT INTO aa_sent_deals VALUES(?,?,?,?,?,?,?)",(r["source_key"],r["source"],r["source_ad_id"],900,1000,10,iso(now_utc())))
        with db(self.m) as c: queue_deal(c,r,e,1); self.assertTrue(suppress_if_legacy(c,self.s,r))
    def test_18_old_legacy_price_not_suppress_new(self):
        r=fx("x",880)
        with sqlite3.connect(self.s) as c: c.execute("INSERT INTO aa_sent_deals VALUES(?,?,?,?,?,?,?)",(r["source_key"],r["source"],r["source_ad_id"],900,1000,10,iso(now_utc())))
        with db(self.m) as c: queue_deal(c,r,Evaluation("deal",3,1000,12),2); self.assertFalse(suppress_if_legacy(c,self.s,r))
    def test_19_reject_not_seeded(self):
        t=iso(now_utc())
        with sqlite3.connect(self.o) as c: c.execute("INSERT INTO listing_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",("divar|x","divar","x","u","t","raw","h",3,0,"s","rejected","bad","unknown","{}",t,t))
        with db(self.m) as c: counts=bootstrap(c,self.s,self.o); n=c.execute("SELECT COUNT(*) FROM market_listings").fetchone()[0]
        self.assertEqual((counts["observation_rows"],n),(0,0))
    def test_20_incomplete_feed_fails_closed(self):
        e=Execution(1,iso(now_utc()-timedelta(minutes=5)),iso(now_utc()),"scheduled",1)
        with db(self.m) as c:
            with self.assertRaisesRegex(RuntimeError,"candidate_feed_incomplete"): process_execution(c,self.s,self.o,e)
    def test_21_dry_run_not_sent(self):
        e=Evaluation("deal",3,1000,10)
        with db(self.m) as c:
            for i in range(3): queue_deal(c,fx(str(i),900),e,1)
            sent,fail=send_all(c,False); sts={r[0] for r in c.execute("SELECT status FROM market_deals")}
        self.assertEqual((sent,fail,sts),(0,0,{"pending"}))
    def test_22_old_history_preserved(self):
        self.put(fx("old",1000,when=now_utc()-timedelta(days=200)))
        with db(self.m) as c: self.assertEqual(c.execute("SELECT COUNT(*) FROM market_listings").fetchone()[0],1)
    def test_23_diagnostic_execution_ignored(self):
        n=now_utc()
        with sqlite3.connect(self.s) as c:
            c.execute("INSERT INTO aa_execution_history VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(1,iso(n-timedelta(minutes=1)),iso(n),"diagnostic","",0,0,0,0,0,0,"success",None))
            c.execute("INSERT INTO aa_execution_history VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(2,iso(n-timedelta(minutes=1)),iso(n),"scheduled","",0,0,0,0,0,0,"success",None))
        self.assertEqual([e.id for e in executions_after(self.s,0)],[2])
    def test_24_obs_roundtrip(self):
        t=iso(now_utc()); p={"brand":"Peugeot","model":"207","trim":"اتوماتیک","model_year":1403,"condition":"used","body_condition":"clean","mileage":50000,"price":900}
        with sqlite3.connect(self.o) as c: c.execute("INSERT INTO listing_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",("telegram|x","telegram","x","u","t","raw","h",3,0,"s","accepted","accepted","clean",json.dumps(p,ensure_ascii=False),t,t))
        with db(self.o,True) as c: r=from_observation(c.execute("SELECT * FROM listing_observations").fetchone())
        self.assertEqual((r["brand"],r["trim"],r["price"]),("Peugeot","اتوماتیک",900))

def self_test():
    r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    print(f"select_carr_market_engine self-test: {'OK' if r.wasSuccessful() else 'FAILED'} tests={r.testsRun}")
    return 0 if r.wasSuccessful() else 1

def main():
    p=argparse.ArgumentParser(); p.add_argument("--self-test",action="store_true"); a=p.parse_args()
    return self_test() if a.self_test else run_engine()

if __name__=="__main__": raise SystemExit(main())
