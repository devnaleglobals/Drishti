"""Material price movement surveillance.

Trigger -> Investigation -> Evidence -> Output

1. A background thread checks every watched stock against its previous close (and the index) during market hours.
2. When a move crosses the threshold (3%, 6%, 9% ... each level triggers once a day), an incident opens,
   an alert email goes out and an investigation starts in the background.
3. The investigation pulls every source for the trigger window, times each item against the start of the move,
   rates source credibility, lets the AI classify and cluster the items, ranks potential rumours,
   makes the material-movement assessment, writes the report and emails it.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import analysis as A
import credibility as CR
import data as D
import notify as N
import profiles as P
import sources as S

BASE = Path(__file__).parent
DATA = Path(os.getenv("DATA_DIR", BASE / "data"))
CONFIG_PATH = DATA / "surveillance.json"
INC_DIR = DATA / "incidents"
_lock = threading.RLock()

DEFAULT_CONFIG = {
    "enabled": True,
    "watchlist": [],            # [{"symbol": "SBICARD.NS", "name": "...", "threshold": null, "enabled": true}]
    "recipients": [],           # alert and report emails
    "threshold": 3.0,           # % move that counts as material (per-stock override allowed)
    "basis": "either",          # "raw" (vs previous close), "adjusted" (minus index move) or "either"
    "interval": 60,             # seconds between checks
    "market_hours_only": True,
    "window_hours": 48,         # how far before the move starts to look for information
    "auto_email_report": True,
}

STEPS = [("trigger", "Trigger detected"), ("alert", "Alert email"), ("collect", "Sources searched"),
         ("classify", "AI classification and clustering"), ("assess", "Assessment"), ("report", "Report written"),
         ("email", "Report emailed")]

LLM: dict = {}  # set by the server


# --------------------------------------------------------------------------
# Config and storage
# --------------------------------------------------------------------------
def load_config() -> dict:
    try:
        cfg = {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text(encoding="utf-8"))}
    except Exception:
        cfg = dict(DEFAULT_CONFIG)
    return cfg


def save_config(body: dict) -> dict:
    cfg = load_config()
    wl = []
    for w in body.get("watchlist", cfg["watchlist"]) or []:
        sym = str(w.get("symbol", "")).strip().upper()
        if not re.match(r"^[A-Z0-9&^._=-]{1,25}$", sym):
            continue
        thr = w.get("threshold")
        try:
            thr = float(thr) if thr not in (None, "") else None
        except ValueError:
            thr = None
        wl.append({"symbol": sym, "name": str(w.get("name") or sym)[:80], "threshold": thr,
                   "enabled": bool(w.get("enabled", True))})
    seen, uniq = set(), []
    for w in wl:
        if w["symbol"] not in seen:
            seen.add(w["symbol"])
            uniq.append(w)
    cfg.update(
        enabled=bool(body.get("enabled", cfg["enabled"])),
        watchlist=uniq[:50],
        recipients=[e.strip() for e in body.get("recipients", cfg["recipients"]) if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(e).strip())][:30],
        threshold=min(max(float(body.get("threshold", cfg["threshold"])), 0.5), 20.0),
        basis=body.get("basis", cfg["basis"]) if body.get("basis", cfg["basis"]) in ("raw", "adjusted", "either") else "either",
        interval=int(min(max(int(body.get("interval", cfg["interval"])), 20), 600)),
        market_hours_only=bool(body.get("market_hours_only", cfg["market_hours_only"])),
        window_hours=int(min(max(int(body.get("window_hours", cfg["window_hours"])), 6), 168)),
        auto_email_report=bool(body.get("auto_email_report", cfg["auto_email_report"])),
    )
    with _lock:
        DATA.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def _inc_path(inc_id: str) -> Path:
    return INC_DIR / (re.sub(r"[^A-Za-z0-9._-]", "_", inc_id) + ".json")


def save_incident(inc: dict) -> None:
    with _lock:
        INC_DIR.mkdir(parents=True, exist_ok=True)
        _inc_path(inc["id"]).write_text(json.dumps(inc, default=str, ensure_ascii=False), encoding="utf-8")


def get_incident(inc_id: str) -> dict | None:
    p = _inc_path(inc_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_incidents(symbol: str = "", limit: int = 200) -> list[dict]:
    if not INC_DIR.exists():
        return []
    out = []
    for p in sorted(INC_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:500]:
        try:
            inc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if symbol and inc["symbol"] != symbol.upper():
            continue
        a = inc.get("assessment") or {}
        out.append({"id": inc["id"], "symbol": inc["symbol"], "name": inc.get("name"), "created": inc["created"],
                    "status": inc["status"], "manual": inc.get("manual", False), "trigger": inc["trigger"],
                    "material": a.get("material"), "rumour": a.get("rumour"), "category": a.get("category"),
                    "driver": a.get("driver")})
    out.sort(key=lambda x: x["created"], reverse=True)
    return out[:limit]


# --------------------------------------------------------------------------
# Monitor
# --------------------------------------------------------------------------
STATE = {"running": False, "last_cycle": None, "next_cycle": None, "market_open": None, "quotes": {}, "levels": {},
         "last_error": None}


def market_open_now() -> bool:
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return 9 * 60 <= t <= 15 * 60 + 40


def _threshold(entry: dict, cfg: dict) -> float:
    return float(entry.get("threshold") or cfg["threshold"])


def _evaluate(q: dict, idx_move, thr: float, basis: str) -> dict:
    move = q.get("change_pct")
    if move is None:
        return {"move": None}
    move *= 100
    adj = move - idx_move * 100 if idx_move is not None else None
    if basis == "raw" or adj is None:
        value = move
    elif basis == "adjusted":
        value = adj
    else:  # either: whichever crosses further
        value = move if abs(move) >= abs(adj) else adj
    level = int(abs(value) // thr) if thr > 0 else 0
    return {"move": move, "adjusted": adj, "index_move": None if idx_move is None else idx_move * 100,
            "value": value, "level": level, "direction": "up" if value > 0 else "down", "threshold": thr}


def check_once(cfg: dict | None = None) -> None:
    cfg = cfg or load_config()
    today = pd.Timestamp.now(tz="Asia/Kolkata").date().isoformat()
    idx_cache = {}
    for entry in cfg["watchlist"]:
        if not entry.get("enabled", True):
            continue
        sym = entry["symbol"]
        try:
            q = D.get_live_quote(sym)
            bsym, bname = D.benchmark_for(sym)
            if bsym not in idx_cache:
                try:
                    idx_cache[bsym] = D.get_live_quote(bsym).get("change_pct")
                except Exception:
                    idx_cache[bsym] = None
            ev = _evaluate(q, idx_cache[bsym], _threshold(entry, cfg), cfg["basis"])
            ev.update(last=q.get("last"), prev_close=q.get("prev_close"), currency=q.get("currency"),
                      benchmark=bname, checked=datetime.now(timezone.utc).isoformat(), market_open=q.get("market_open"))
            STATE["quotes"][sym] = ev
            if ev.get("move") is None or ev["level"] < 1:
                continue
            lv = STATE["levels"].setdefault(sym, {})
            if lv.get("date") != today:
                lv.clear()
                lv.update(date=today, up=0, down=0)
            if ev["level"] > lv[ev["direction"]]:
                lv[ev["direction"]] = ev["level"]
                start_incident(sym, entry.get("name") or sym, ev, manual=False)
        except Exception as e:
            STATE["last_error"] = f"{sym}: {str(e)[:160]}"


def _loop():
    STATE["running"] = True
    while True:
        cfg = load_config()
        try:
            is_open = market_open_now()
            STATE["market_open"] = is_open
            if cfg["enabled"] and cfg["watchlist"] and (is_open or not cfg["market_hours_only"]):
                check_once(cfg)
            STATE["last_cycle"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            STATE["last_error"] = str(e)[:200]
        STATE["next_cycle"] = (datetime.now(timezone.utc) + timedelta(seconds=cfg["interval"])).isoformat()
        time.sleep(cfg["interval"])


_started = False


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="surveillance-monitor", daemon=True).start()


def status() -> dict:
    cfg = load_config()
    return {"running": STATE["running"], "last_cycle": STATE["last_cycle"], "next_cycle": STATE["next_cycle"],
            "market_open": market_open_now(), "quotes": STATE["quotes"], "last_error": STATE["last_error"],
            "email": N.method(), "config": cfg}


# --------------------------------------------------------------------------
# Incidents
# --------------------------------------------------------------------------
def _app_link(inc: dict) -> str:
    base = os.getenv("APP_BASE_URL", "").rstrip("/")
    return f"{base}/#{inc['symbol']}/incidents/{inc['id']}" if base else ""


def _step(inc: dict, key: str, state: str, note: str = "") -> None:
    for s in inc["steps"]:
        if s["key"] == key:
            s.update(state=state, note=note, at=datetime.now(timezone.utc).isoformat())
    save_incident(inc)


def start_incident(symbol: str, name: str, ev: dict, manual: bool) -> str:
    now = datetime.now(timezone.utc)
    inc = {
        "id": f"{now:%Y%m%d-%H%M%S}-{re.sub(r'[^A-Z0-9]', '', symbol.upper())}-{uuid.uuid4().hex[:4]}",
        "symbol": symbol.upper(), "name": name, "created": now.isoformat(), "status": "investigating",
        "manual": manual, "trigger": {**ev, "time": now.isoformat()},
        "steps": [{"key": k, "label": l, "state": "pending", "note": "", "at": None} for k, l in STEPS],
        "emails": [],
    }
    _step(inc, "trigger", "done", "Started manually" if manual else
          f"{ev['value']:+.2f}% crossed the {ev['threshold']:.1f}% threshold")
    threading.Thread(target=_run, args=(inc["id"],), daemon=True).start()
    return inc["id"]


def investigate_now(symbol: str) -> str:
    cfg = load_config()
    entry = next((w for w in cfg["watchlist"] if w["symbol"] == symbol.upper()), {"symbol": symbol.upper()})
    q = D.get_live_quote(symbol)
    bsym, bname = D.benchmark_for(symbol)
    try:
        idx = D.get_live_quote(bsym).get("change_pct")
    except Exception:
        idx = None
    ev = _evaluate(q, idx, _threshold(entry, cfg), cfg["basis"])
    if ev.get("move") is None:
        raise ValueError("No live price available for this symbol right now")
    ev.update(last=q.get("last"), prev_close=q.get("prev_close"), currency=q.get("currency"), benchmark=bname)
    name = entry.get("name") or D.get_profile(symbol).get("name", symbol)
    return start_incident(symbol, name, ev, manual=True)


def _alert_email(inc: dict) -> None:
    cfg = load_config()
    t = inc["trigger"]
    arrow = "▲" if t["value"] > 0 else "▼"
    adj = f" ({t['adjusted']:+.2f}% vs {t.get('benchmark', 'index')})" if t.get("adjusted") is not None else ""
    subj = f"[Drishti] Material price movement: {inc['symbol']} {arrow} {abs(t['move']):.2f}%{adj}"
    when = pd.Timestamp(t["time"]).tz_convert("Asia/Kolkata").strftime("%d %b %Y, %H:%M IST")
    link = _app_link(inc)
    idx_txt = "" if t.get("index_move") is None else "%+.2f%%" % t["index_move"]
    adj_txt = "" if t.get("adjusted") is None else "<b>%+.2f%%</b>" % t["adjusted"]
    link_txt = ('<p><a href="%s">Open the incident in Drishti</a></p>' % html.escape(link)) if link else ""
    body = f"""<div style="font-family:Arial,sans-serif;font-size:14px;color:#16202C">
<h2 style="margin:0 0 8px">{html.escape(inc['name'])} ({inc['symbol']}) {arrow} {t['move']:+.2f}%</h2>
<p style="margin:0 0 12px;color:#5B6B7F">{'Manual investigation' if inc['manual'] else 'Automatic trigger'} at {when}</p>
<table cellpadding="6" style="border-collapse:collapse;font-size:14px">
<tr><td style="color:#5B6B7F">Last price</td><td><b>{t.get('last') or 0:,.2f}</b> {html.escape(t.get('currency') or '')}</td></tr>
<tr><td style="color:#5B6B7F">Previous close</td><td>{t.get('prev_close') or 0:,.2f}</td></tr>
<tr><td style="color:#5B6B7F">Move vs previous close</td><td><b>{t['move']:+.2f}%</b></td></tr>
<tr><td style="color:#5B6B7F">{html.escape(t.get('benchmark') or 'Index')} move</td><td>{idx_txt}</td></tr>
<tr><td style="color:#5B6B7F">Index-adjusted move</td><td>{adj_txt}</td></tr>
<tr><td style="color:#5B6B7F">Threshold</td><td>{t['threshold']:.1f}% ({cfg['basis']})</td></tr></table>
<p>The investigation has started: news, filings, social media and forums are being searched for the trigger window.
The evidence-based report will follow by email.</p>
{link_txt}
<p style="color:#8DA2BA;font-size:12px">Automated surveillance alert. For internal assessment; not investment advice.</p></div>"""
    ok, msg = N.send(cfg["recipients"], subj, body)
    inc["emails"].append({"type": "alert", "ok": ok, "message": msg, "at": datetime.now(timezone.utc).isoformat()})
    _step(inc, "alert", "done" if ok else "skipped", msg)


# --------------------------------------------------------------------------
# Investigation
# --------------------------------------------------------------------------
def _price_context(symbol: str, trigger: dict) -> dict:
    q = D.get_live_quote(symbol)
    intr = q.get("intraday")
    prev = q.get("prev_close") or trigger.get("prev_close")
    out = {"prev_close": prev, "last": q.get("last"), "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
           "series": [], "onset": None, "pre_930": None, "post_930": None, "split_label": "Pre-9:30",
           "volume_ratio": None, "price_930": None}
    if intr is None or intr.empty or not prev:
        return out
    close = intr["Close"].dropna()
    out["series"] = [{"time": int(t.timestamp() + (t.utcoffset().total_seconds() if t.tzinfo else 0)), "value": float(v)}
                     for t, v in close.items()]
    first = close.index[0]
    cut = first.normalize() + pd.Timedelta(hours=9, minutes=30) if D.is_indian(symbol) else first + pd.Timedelta(minutes=15)
    if not D.is_indian(symbol):
        out["split_label"] = "First 15 minutes"
    early = close[close.index <= cut]
    if len(early):
        p930 = float(early.iloc[-1])
        out["price_930"] = p930
        out["pre_930"] = p930 / prev - 1
        out["post_930"] = float(close.iloc[-1]) / p930 - 1
    thr = trigger.get("threshold", 3.0) / 100
    sign = 1 if trigger.get("value", trigger.get("move", 0)) >= 0 else -1
    rel = (close / prev - 1) * sign
    hit = rel[rel >= thr * 0.3]  # the move "starts" once 30% of the threshold is covered
    onset = hit.index[0] if len(hit) else close.index[0]
    out["onset"] = onset.tz_convert("UTC").isoformat()
    out["onset_local"] = onset.strftime("%H:%M")
    try:
        daily = D.get_history(symbol, "3mo")
        avg = daily["Volume"].iloc[-21:-1].mean()
        today_vol = float(intr["Volume"].sum())
        if avg and avg > 0:
            out["volume_ratio"] = today_vol / avg
    except Exception:
        pass
    return out


_STOP = set("the a an and or of to in on for with at by from as is are was be has have its it this that after over amid "
            "shares share stock stocks price prices nse bse ltd limited india indian says said report reports news".split())


def _tokens(t: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]{3,}", t.lower()) if w not in _STOP}


def _heuristic(items: list[dict], direction: str) -> dict:
    """Fallback when AI is not configured: keyword tone + simple title clustering."""
    clusters, res = [], {}
    for it in items:
        toks = _tokens(it["title"])
        best, score = None, 0.0
        for c in clusters:
            j = len(toks & c["tokens"]) / max(1, len(toks | c["tokens"]))
            if j > score:
                best, score = c, j
        if best is None or score < 0.3:
            best = {"cluster": f"c{len(clusters) + 1}", "tokens": set(toks), "label": it["title"][:80]}
            clusters.append(best)
        else:
            best["tokens"] |= toks
        tone = A.keyword_sentiment(it["title"] + " " + it.get("summary", ""))
        typ = "official_disclosure" if it["kind"] == "filing" else "social_chatter" if it["tier"] == "social" else "confirmed_news"
        if re.search(r"\b(sources|reportedly|likely|may|rumou?r|speculat|buzz|talks|could)\b", it["title"], re.I) and typ != "official_disclosure":
            typ = "unverified_rumour"
        res[it["id"]] = {"relevant": True, "direction": "positive" if tone > 0.15 else "negative" if tone < -0.15 else "neutral",
                         "type": typ, "confidence": 0.4, "cluster": best["cluster"]}
    meta = []
    for c in clusters:
        mem = [res[i] for i in res if res[i]["cluster"] == c["cluster"]]
        dirs = [m["direction"] for m in mem]
        d = max(set(dirs), key=dirs.count) if dirs else "neutral"
        types = [m["type"] for m in mem]
        t = max(set(types), key=types.count) if types else "confirmed_news"
        meta.append({"cluster": c["cluster"], "label": c["label"], "summary": "", "direction": d, "type": t,
                     "confidence": 0.4, "is_potential_rumour": t in ("unverified_rumour", "social_chatter")})
    return {"items": res, "clusters": meta}


def _cluster_score(c: dict, onset: pd.Timestamp, move_dir: str) -> float:
    conf = float(c.get("confidence") or 0.3)
    tw = max(CR.TIERS[t]["weight"] for t in c["tiers"]) if c["tiers"] else 0.5
    first = pd.Timestamp(c["first_seen"])
    lead = (onset - first).total_seconds() / 3600
    timing = 1.0 if 0 <= lead <= 24 else 0.8 if lead > 24 else (0.6 if lead >= -1 else 0.4)
    want = "positive" if move_dir == "up" else "negative"
    dmatch = 1.0 if c["direction"] == want else 0.55 if c["direction"] == "neutral" else 0.3
    spread = 1 + 0.15 * math.log1p(max(0, c["outlets"] - 1))
    if c["type"] == "unrelated":
        return 0.0
    return round(min(1.0, conf * tw * timing * dmatch * spread), 3)


def _run(inc_id: str) -> None:
    inc = get_incident(inc_id)
    try:
        _alert_email(inc)
        _investigate(inc)
    except Exception as e:
        traceback.print_exc()
        inc = get_incident(inc_id) or inc
        inc["status"] = "failed"
        inc["error"] = str(e)[:300]
        for s in inc["steps"]:
            if s["state"] in ("pending", "running"):
                s["state"] = "failed" if s["state"] == "running" else "skipped"
        save_incident(inc)


def _investigate(inc: dict) -> None:
    cfg = load_config()
    sym, t = inc["symbol"], inc["trigger"]
    move_dir = "up" if t["value"] > 0 else "down"

    # 1. Price and timing context ---------------------------------------
    _step(inc, "collect", "running", "Searching news, filings, social media and forums")
    price = _price_context(sym, t)
    inc["price"] = price
    onset = pd.Timestamp(price["onset"]) if price.get("onset") else pd.Timestamp(t["time"])
    trig_time = pd.Timestamp(t["time"])

    # 2. Collect from every source ---------------------------------------
    info = D.get_profile(sym)
    kp = P.load(sym, info)
    region = "IN" if D.is_indian(sym) else "US"
    days = max(2, math.ceil(cfg["window_hours"] / 24) + 1)
    items, health = S.fetch_all(sym, kp, days, region)
    start = onset - pd.Timedelta(hours=cfg["window_hours"])
    items = [i for i in items if i["published"] >= start]
    ev = []
    for n, it in enumerate(items):
        tier = CR.tier_of(it["source"], it["url"], it["kind"])
        mins = int((it["published"] - onset).total_seconds() // 60)
        ev.append({"id": n, "title": it["title"], "summary": it.get("summary", ""), "source": it["source"],
                   "url": it["url"], "published": it["published"].isoformat(), "kind": it["kind"], "tier": tier,
                   "connectors": it.get("connectors", []), "coverage": it.get("coverage", 1),
                   "engagement": it.get("engagement"), "reach": it.get("reach"), "minutes_vs_onset": mins,
                   "time": it["published"].tz_convert("Asia/Kolkata").strftime("%d %b %H:%M IST")})
    inc["health"] = [{k: h[k] for k in ("id", "name", "kind", "status", "count", "note")} for h in health]
    working = sum(1 for h in health if h["status"] == "ok")
    _step(inc, "collect", "done", f"{len(ev)} items in the window from {working} sources")

    # 3. Classify and cluster --------------------------------------------
    _step(inc, "classify", "running", "")
    ranked_input = sorted(ev, key=lambda x: abs(x["minutes_vs_onset"]))[:120]
    used_ai = False
    result = None
    if A.llm_ready(LLM) and ranked_input:
        try:
            trig_txt = (f"{t['move']:+.2f}% vs previous close; {t.get('index_move') or 0:+.2f}% for {t.get('benchmark', 'index')}; "
                        f"move started around {price.get('onset_local', '?')} exchange time; detected {trig_time.tz_convert('Asia/Kolkata'):%H:%M IST}")
            result = A.ai_investigate(LLM, inc["name"], sym, trig_txt, ranked_input)
            used_ai = True
        except Exception as e:
            print(f"[surveillance] AI classification failed: {e}", flush=True)
    if result is None:
        result = _heuristic(ranked_input, move_dir)
    scored_ids = {x["id"] for x in ranked_input}
    for it in ev:
        r = result["items"].get(it["id"]) if it["id"] in scored_ids else None
        it.update(relevant=bool(r.get("relevant", True)) if r else True,
                  direction=(r or {}).get("direction", "neutral"), type=(r or {}).get("type", "confirmed_news"),
                  confidence=float((r or {}).get("confidence", 0.2)), cluster=(r or {}).get("cluster", "other"),
                  classified=bool(r))
    irrelevant = sum(1 for it in ev if not it["relevant"] or it["type"] == "unrelated")
    ev = [it for it in ev if it["relevant"] and it["type"] != "unrelated"]

    clusters = []
    meta = {c.get("cluster"): c for c in result.get("clusters", [])}
    for cid in sorted({it["cluster"] for it in ev}):
        mem = sorted([it for it in ev if it["cluster"] == cid], key=lambda x: x["published"])
        m = meta.get(cid, {})
        c = {"cluster": cid, "label": m.get("label") or mem[0]["title"][:90], "summary": m.get("summary", ""),
             "direction": m.get("direction") or mem[0]["direction"], "type": m.get("type") or mem[0]["type"],
             "confidence": float(m.get("confidence", max(i["confidence"] for i in mem))),
             "is_potential_rumour": bool(m.get("is_potential_rumour", mem[0]["type"] in ("unverified_rumour", "social_chatter"))),
             "items": [i["id"] for i in mem], "first_seen": mem[0]["published"], "first_source": mem[0]["source"],
             "first_time": mem[0]["time"], "outlets": len({i["source"] for i in mem}),
             "tiers": sorted({i["tier"] for i in mem}, key=lambda x: list(CR.TIERS).index(x)),
             "engagement": sum(int(i.get("engagement") or 0) for i in mem),
             "before_onset": mem[0]["minutes_vs_onset"] <= 0,
             "last_seen": mem[-1]["published"]}
        c["score"] = _cluster_score(c, onset, move_dir)
        clusters.append(c)
    clusters.sort(key=lambda c: c["score"], reverse=True)
    inc["clusters"] = clusters
    inc["evidence"] = sorted(ev, key=lambda x: x["published"])
    inc["used_ai"] = used_ai
    _step(inc, "classify", "done", f"{len(clusters)} stories from {len(ev)} relevant items"
          + (f", {irrelevant} unrelated removed" if irrelevant else "") + ("" if used_ai else " (keyword method: AI not connected)"))

    # 4. Assessment --------------------------------------------------------
    _step(inc, "assess", "running")
    thr = t["threshold"]
    want = "positive" if move_dir == "up" else "negative"
    material = abs(t["value"]) >= thr
    market_wide = t.get("adjusted") is not None and abs(t["move"]) >= thr and abs(t["adjusted"]) < thr
    official = [c for c in clusters if c["type"] == "official_disclosure" and c["direction"] in (want, "neutral")
                and pd.Timestamp(c["first_seen"]) <= onset + pd.Timedelta(minutes=30) and c["score"] >= 0.2]
    rumours = [c for c in clusters if c["is_potential_rumour"] and c["score"] >= 0.25]
    in_mainstream = [c for c in rumours if "mainstream" in c["tiers"]]
    news = [c for c in clusters if c["type"] == "confirmed_news" and c["direction"] == want and c["score"] >= 0.3]
    top = clusters[0] if clusters and clusters[0]["score"] >= 0.2 else None

    if market_wide and not rumours:
        cat, action = ("Market-wide movement",
                       f"The move is largely explained by the {t.get('benchmark', 'index')}. Continue routine monitoring.")
    elif official:
        cat, action = ("Explained by exchange disclosure",
                       "The move follows a disclosure already on the exchange. Verify the filing covers the market reaction; no rumour action indicated.")
    elif in_mainstream:
        cat, action = ("Potential rumour in mainstream media",
                       "Information not covered by an exchange disclosure is circulating in mainstream media. Consider seeking confirmation, denial or clarification from the company.")
    elif rumours:
        cat, action = ("Potential rumour on social media or online sources",
                       "Unverified information is circulating outside mainstream media. Monitor its spread; escalate if it reaches mainstream media.")
    elif news:
        cat, action = ("Explained by public news",
                       "Confirmed public news explains the direction of the move. Check whether a related exchange disclosure is due.")
    else:
        cat, action = ("No public information found",
                       "No public information explains the move. Consider seeking clarification from the company and checking trading patterns.")
    basis_txt = {"raw": "vs previous close", "adjusted": "after removing the index move", "either": "raw or index-adjusted"}[cfg["basis"]]
    inc["assessment"] = {
        "material": material, "rumour": bool(rumours), "rumour_in_mainstream": bool(in_mainstream),
        "category": cat, "action": action, "driver": top["label"] if top else None,
        "driver_score": top["score"] if top else 0, "market_wide": market_wide,
        "material_reason": (f"{abs(t['value']):.2f}% move ({basis_txt}) {'meets' if material else 'is below'} the {thr:.1f}% threshold"),
    }
    _step(inc, "assess", "done", cat)

    # 5. Report ---------------------------------------------------------------
    _step(inc, "report", "running")
    ctx = _report_context(inc)
    md = None
    if A.llm_ready(LLM):
        try:
            md = A.ai_incident_report(LLM, ctx)
        except Exception as e:
            print(f"[surveillance] AI report failed: {e}", flush=True)
    inc["report_md"] = md or _template_report(inc)
    inc["report_ai"] = bool(md)
    inc["status"] = "complete"
    inc["completed"] = datetime.now(timezone.utc).isoformat()
    inc["duration_min"] = round((pd.Timestamp(inc["completed"]) - pd.Timestamp(inc["created"])).total_seconds() / 60, 1)
    _step(inc, "report", "done", f"Ready {inc['duration_min']} min after the trigger")

    # 6. Email -----------------------------------------------------------------
    if cfg["auto_email_report"]:
        ok, msg = email_report(inc, cfg["recipients"])
        _step(inc, "email", "done" if ok else "skipped", msg)
    else:
        _step(inc, "email", "skipped", "Automatic report email is switched off")


def _report_context(inc: dict) -> str:
    t, p, a = inc["trigger"], inc.get("price", {}), inc["assessment"]

    def pc(v):
        return "n/a" if v is None else f"{v * 100:+.2f}%"

    idx_txt = "n/a" if t.get("index_move") is None else "%+.2f%%" % t["index_move"]
    adj_txt = "n/a" if t.get("adjusted") is None else "%+.2f%%" % t["adjusted"]
    vol_txt = "n/a" if not p.get("volume_ratio") else "%.1fx the 20-day average" % p["volume_ratio"]
    lines = [f"Company: {inc['name']} ({inc['symbol']})",
             f"Trigger: {'manual' if inc['manual'] else 'automatic'} at {pd.Timestamp(t['time']).tz_convert('Asia/Kolkata'):%d %b %Y %H:%M IST}",
             f"Move vs previous close {t['move']:+.2f}%; {t.get('benchmark', 'index')} {idx_txt}; "
             f"index-adjusted {adj_txt}; threshold {t['threshold']:.1f}%",
             f"{p.get('split_label', 'Pre-9:30')} move {pc(p.get('pre_930'))}; after that {pc(p.get('post_930'))}; "
             f"move started around {p.get('onset_local', 'n/a')} exchange time; volume {vol_txt}",
             f"Computed assessment: material movement {'YES' if a['material'] else 'NO'} ({a['material_reason']}); "
             f"category: {a['category']}; potential rumour: {'YES' if a['rumour'] else 'NO'}"
             f"{' (in mainstream media)' if a.get('rumour_in_mainstream') else ''}; suggested action: {a['action']}",
             "", "RANKED STORIES (score 0-1):"]
    ev = {e["id"]: e for e in inc.get("evidence", [])}
    for c in inc.get("clusters", [])[:8]:
        lines.append(f"- [{c['score']:.2f}] {c['label']} | {c['type']} | {c['direction']} | first seen {c['first_time']} via {c['first_source']} "
                     f"({'before' if c['before_onset'] else 'after'} the move started) | {c['outlets']} outlets | tiers {', '.join(c['tiers'])}"
                     + (f" | {c['summary']}" if c.get("summary") else ""))
        for i in c["items"][:4]:
            e = ev.get(i)
            if e:
                lines.append(f"    * {e['time']} {e['source']}: {e['title'][:140]}")
    return "\n".join(lines)


def _template_report(inc: dict) -> str:
    t, p, a = inc["trigger"], inc.get("price", {}), inc["assessment"]
    out = [f"## Summary",
           f"{inc['name']} moved {t['move']:+.2f}% against its previous close"
           + (f" ({t['adjusted']:+.2f}% after removing the {t.get('benchmark', 'index')} move)" if t.get("adjusted") is not None else "")
           + f". Material movement: **{'Yes' if a['material'] else 'No'}**. {a['category']}.",
           "## Ranked stories"]
    for c in inc.get("clusters", [])[:5]:
        out.append(f"- **{c['label']}** ({c['type'].replace('_', ' ')}, {c['direction']}, score {c['score']:.2f}): first seen "
                   f"{c['first_time']} via {c['first_source']}, {c['outlets']} outlet(s).")
    if not inc.get("clusters"):
        out.append("- No relevant public information found in the window.")
    out += ["## Assessment", a["material_reason"] + ". " + a["action"],
            "## Limitations", "Written without AI (not connected). Free data sources can be delayed or incomplete."]
    return "\n".join(out)


# --------------------------------------------------------------------------
# Report rendering and email
# --------------------------------------------------------------------------
def _md(text: str) -> str:
    try:
        import markdown
        out = markdown.markdown(html.escape(text or "").replace("&gt;", ">"), extensions=["tables"])
        return (out.replace("<h2>", '<h3 style="font-size:15px;margin:14px 0 4px">').replace("</h2>", "</h3>")
                .replace("<h1>", '<h3 style="font-size:15px;margin:14px 0 4px">').replace("</h1>", "</h3>"))
    except Exception:
        return "<p>" + html.escape(text or "").replace("\n", "<br>") + "</p>"


def report_html(inc: dict, full: bool = True) -> str:
    """Self-contained report (full=True) or the shorter email body (full=False). Light theme, prints well."""
    t, p, a = inc["trigger"], inc.get("price", {}), inc.get("assessment") or {}
    esc = html.escape
    when = pd.Timestamp(t["time"]).tz_convert("Asia/Kolkata").strftime("%d %b %Y, %H:%M IST")

    def pc(v, mult=1):
        return "–" if v is None else f"{v * mult:+.2f}%"

    yes = lambda b: f'<b style="color:{"#B42318" if b else "#067647"}">{"YES" if b else "NO"}</b>'
    rows = [("Move vs previous close", pc(t.get("move"))), (f"{t.get('benchmark', 'Index')} move", pc(t.get("index_move"))),
            ("Index-adjusted move", pc(t.get("adjusted"))), (p.get("split_label", "Pre-9:30") + " move", pc(p.get("pre_930"), 100)),
            ("Move after that", pc(p.get("post_930"), 100)), ("Move started (exchange time)", p.get("onset_local") or "–"),
            ("Volume vs 20-day average", f"{p['volume_ratio']:.1f}x" if p.get("volume_ratio") else "–"),
            ("Threshold", f"{t['threshold']:.1f}%")]
    kv = "".join(f'<tr><td style="padding:6px 10px;color:#5B6B7F">{esc(k)}</td><td style="padding:6px 10px"><b>{esc(str(v))}</b></td></tr>' for k, v in rows)
    ev = {e["id"]: e for e in inc.get("evidence", [])}
    cl = ""
    for c in inc.get("clusters", [])[: (8 if full else 3)]:
        srcs = "".join(f'<li><a href="{esc(ev[i]["url"] or "#")}">{esc(ev[i]["title"][:140])}</a> <span style="color:#5B6B7F">{esc(ev[i]["source"])}, {esc(ev[i]["time"])}</span></li>'
                       for i in c["items"][:4] if i in ev)
        cl += (f'<div style="border:1px solid #D0D7E2;border-left:4px solid {"#B42318" if c["is_potential_rumour"] else "#1F5FAD"};border-radius:6px;padding:10px 14px;margin:10px 0">'
               f'<div style="font-weight:bold">{esc(c["label"])}</div>'
               f'<div style="color:#5B6B7F;font-size:13px;margin:4px 0">Score {c["score"]:.2f} | {esc(c["type"].replace("_", " "))} | {esc(c["direction"])} | '
               f'first seen {esc(c["first_time"])} via {esc(c["first_source"])} ({"before" if c["before_onset"] else "after"} the move started) | '
               f'{c["outlets"]} outlet(s) | {esc(", ".join(CR.TIERS[x]["label"] for x in c["tiers"]))}</div>'
               + (f'<div style="font-size:14px">{esc(c["summary"])}</div>' if c.get("summary") else "")
               + f'<ul style="margin:6px 0 0;padding-left:18px;font-size:13px">{srcs}</ul></div>')
    if not cl:
        cl = "<p>No relevant public information found in the trigger window.</p>"
    link = _app_link(inc)
    head = (f'<h1 style="font-size:22px;margin:0">{esc(inc["name"])} ({esc(inc["symbol"])}) {"▲" if t["value"] > 0 else "▼"} {t["move"]:+.2f}%</h1>'
            f'<p style="color:#5B6B7F;margin:4px 0 16px">Material price movement report. Trigger {when}'
            f'{" (manual)" if inc.get("manual") else ""}. Report ready {inc.get("duration_min", "–")} min after the trigger.</p>'
            f'<table style="border-collapse:collapse;margin-bottom:14px;font-size:15px">'
            f'<tr><td style="padding:6px 10px">Material movement</td><td style="padding:6px 10px">{yes(a.get("material"))}</td></tr>'
            f'<tr><td style="padding:6px 10px">Potential rumour</td><td style="padding:6px 10px">{yes(a.get("rumour"))}'
            f'{" (mainstream media)" if a.get("rumour_in_mainstream") else ""}</td></tr>'
            f'<tr><td style="padding:6px 10px">Assessment</td><td style="padding:6px 10px"><b>{esc(a.get("category", ""))}</b></td></tr>'
            f'<tr><td style="padding:6px 10px">Suggested action</td><td style="padding:6px 10px">{esc(a.get("action", ""))}</td></tr></table>')
    body = head + f'<h2 style="font-size:17px">Price and index</h2><table style="border-collapse:collapse;font-size:14px">{kv}</table>' \
        + f'<h2 style="font-size:17px">Potential rumours and market information (ranked)</h2>{cl}'
    if full:
        body += f'<h2 style="font-size:17px">Analyst report</h2><div style="font-size:14px;line-height:1.6">{_md(inc.get("report_md", ""))}</div>'
        rows_ev = "".join(
            f'<tr><td>{esc(e["time"])}</td><td>{e["minutes_vs_onset"]:+d}</td><td>{esc(e["source"])}</td><td>{esc(CR.TIERS[e["tier"]]["label"])}</td>'
            f'<td>{esc(e.get("type", "").replace("_", " "))}</td><td>{esc(e.get("direction", ""))}</td><td>{e.get("confidence", 0):.2f}</td>'
            f'<td>{"" if not e.get("engagement") else e["engagement"]}</td><td><a href="{esc(e["url"] or "#")}">{esc(e["title"][:120])}</a></td></tr>'
            for e in inc.get("evidence", []))
        body += ('<h2 style="font-size:17px">Evidence trail</h2><table border="1" cellpadding="5" style="border-collapse:collapse;font-size:12px;border-color:#D0D7E2">'
                 '<tr style="background:#EEF2F7"><th>Time</th><th>Min vs start</th><th>Source</th><th>Credibility</th><th>Type</th><th>Direction</th>'
                 f'<th>Confidence</th><th>Engagement</th><th>Item</th></tr>{rows_ev}</table>')
        body += ('<h2 style="font-size:17px">Method and limitations</h2><p style="font-size:13px;color:#5B6B7F">Trigger: price vs previous close and index-adjusted '
                 'move against the configured threshold. Investigation: public news, exchange and regulator filings, forums, video and social sources in the '
                 'window before and after the move started; items are timed against the start of the move, rated for source credibility, classified and '
                 'clustered by AI, and ranked. Supports the material price movement and market-rumour verification process (SEBI LODR Regulation 30(11), '
                 'NSE/SURV/62122 as configured); the final assessment rests with the surveillance team. Free data sources can be delayed or incomplete.</p>')
    else:
        link_txt = (' and available in Drishti: <a href="%s">open the incident</a>' % esc(link)) if link else ""
        body += f'<p style="font-size:14px">The full report with the analyst write-up and evidence trail is attached{link_txt}.</p>' 
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{esc(inc["symbol"])} movement report</title></head>'
            f'<body style="font-family:Arial,Helvetica,sans-serif;color:#16202C;max-width:960px;margin:0 auto;padding:20px">{body}'
            f'<p style="color:#8DA2BA;font-size:12px;margin-top:24px">Generated by Drishti surveillance. For internal assessment; not investment advice.</p></body></html>')


def email_report(inc: dict, recipients: list[str]) -> tuple[bool, str]:
    a = inc.get("assessment") or {}
    t = inc["trigger"]
    subj = (f"[Drishti] Report: {inc['symbol']} {t['move']:+.2f}%: "
            f"{'Material' if a.get('material') else 'Not material'}, {a.get('category', 'assessment')}")
    full = report_html(inc, full=True).encode("utf-8")
    ok, msg = N.send(recipients, subj, report_html(inc, full=False),
                     attachment=(f"{inc['symbol']}_{inc['id'][:15]}_report.html", full, "html"))
    inc.setdefault("emails", []).append({"type": "report", "ok": ok, "message": msg, "to": recipients,
                                         "at": datetime.now(timezone.utc).isoformat()})
    save_incident(inc)
    return ok, msg
