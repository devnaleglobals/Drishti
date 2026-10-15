"""Headline Impact backend.

Run:  python server.py      (opens http://127.0.0.1:8000 in your browser)
"""
from __future__ import annotations

import asyncio
import base64
import json
import secrets
import math
import os
import threading
import time
import webbrowser
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

import analysis as A  # noqa: E402
import data as D  # noqa: E402
import lenses as L  # noqa: E402
import profiles as P  # noqa: E402
import settings as SET  # noqa: E402
import sources as S  # noqa: E402
import notify as N  # noqa: E402
import surveillance as SV  # noqa: E402

app = FastAPI(title="Headline Impact")


class BasicAuth:
    """Asks for a username and password before showing anything (enabled when APP_PASSWORD is set)."""

    def __init__(self, app, user: str, password: str):
        self.app, self.user, self.password = app, user, password

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/healthz":
            return await self.app(scope, receive, send)
        auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
        ok = False
        if auth.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, self.user) and secrets.compare_digest(pw, self.password)
            except Exception:
                ok = False
        if ok:
            return await self.app(scope, receive, send)
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"www-authenticate", b'Basic realm="Headline Impact"'), (b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"Login required"})


if os.getenv("APP_PASSWORD"):
    app.add_middleware(BasicAuth, user=os.getenv("APP_USERNAME", "admin"), password=os.getenv("APP_PASSWORD"))


@app.get("/healthz")
def healthz():
    return {"ok": True}

# --------------------------------------------------------------------------
# AI configuration (read from .env, never sent to the browser)
# --------------------------------------------------------------------------
def load_llm() -> dict:
    az = {
        "provider": "azure",
        "api_key": os.getenv("AZURE_OPENAI_API_KEY", ""),
        "endpoint": os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        "deployment": os.getenv("AZURE_OPENAI_DEPLOYMENT", ""),
        "api_version": os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
    }
    an = {
        "provider": "anthropic",
        "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
        "model": os.getenv("ANTHROPIC_MODEL", A.DEFAULT_MODEL),
    }
    choice = os.getenv("AI_PROVIDER", "").lower()
    if choice == "anthropic":
        return an
    if choice == "azure":
        return az
    return az if A.llm_ready(az) else an


LLM = load_llm()
# The name users see for the AI. The real provider and model are never shown.
AI_NAME = os.getenv("AI_DISPLAY_NAME", "AI Analyst")
LLM["display_name"] = AI_NAME
SV.LLM = LLM


def _ai_fail(where: str, e: Exception) -> str:
    """Log the real error on the server; give users a message without provider details."""
    print(f"[AI error in {where}] {e}", flush=True)
    return "The AI service is not responding right now. Please try again in a minute."

# --------------------------------------------------------------------------
# Small in-memory cache
# --------------------------------------------------------------------------
_cache: dict = {}
_lock = threading.Lock()


def cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def clean(o):
    """Make pandas/numpy values JSON-safe."""
    if o is None or o is pd.NaT:
        return None
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if (math.isnan(o) or math.isinf(o)) else float(o)
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    return o


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@app.get("/api/config")
def config():
    return {"ai_enabled": A.llm_ready(LLM), "label": AI_NAME}


@app.get("/api/search")
def search(q: str):
    if not q.strip():
        return []
    return cached(("search", q.lower()), 3600, lambda: D.search_symbols(q))


@app.get("/api/profile/{symbol}")
def profile(symbol: str):
    p = cached(("profile", symbol), 3600, lambda: D.get_profile(symbol))
    bsym, bname = D.benchmark_for(symbol)
    return clean({**p, "benchmark": bname, "benchmark_symbol": bsym})


def quote_payload(symbol: str) -> dict:
    q = cached(("quote", symbol), 3, lambda: D.get_live_quote(symbol))
    pts = []
    intr = q.get("intraday")
    if intr is not None and not intr.empty:
        for t, v in zip(intr.index, intr["Close"]):
            if pd.isna(v):
                continue
            off = t.utcoffset().total_seconds() if t.tzinfo else 0
            pts.append({"time": int(t.timestamp() + off), "value": float(v)})  # exchange wall-clock time
    out = {k: q.get(k) for k in ("last", "prev_close", "open", "high", "low", "volume", "currency",
                                  "change", "change_pct", "market_open")}
    out["as_of"] = q["as_of"].strftime("%d %b %Y, %H:%M") if q.get("as_of") is not None else None
    out["intraday"] = pts
    out["checked"] = datetime.now().strftime("%H:%M:%S")
    return clean(out)


@app.get("/api/quote/{symbol}")
def quote(symbol: str):
    return quote_payload(symbol)


@app.get("/api/stream/{symbol}")
async def stream(symbol: str, interval: int = 10):
    """Server-Sent Events: pushes a fresh quote every `interval` seconds."""
    interval = max(3, min(interval, 120))

    async def gen():
        while True:
            try:
                payload = await asyncio.to_thread(quote_payload, symbol)
                yield f"data: {json.dumps(payload)}\n\n"
            except Exception as e:  # keep the stream alive through temporary errors
                yield f"event: problem\ndata: {json.dumps({'error': str(e)[:200]})}\n\n"
            await asyncio.sleep(interval)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _events_records(ev: pd.DataFrame) -> list[dict]:
    if ev.empty:
        return []
    num = ["sentiment", "importance", "ret_d0", "abn_d0", "ret_d1", "abn_d1", "ret_w", "abn_w",
           "vol_ratio", "first_hour", "move_vs_normal", "key_move"]
    recs = []
    for _, r in ev.iterrows():
        rec = {
            "id": int(r["id"]),
            "published": r["published"].isoformat() if pd.notna(r["published"]) else None,
            "title": r["title"], "source": r["source"], "url": r["url"],
            "category": r["category"] if isinstance(r["category"], str) else "",
            "why": r["why"] if isinstance(r["why"], str) else "",
            "event_day": r["event_day"].strftime("%Y-%m-%d") if pd.notna(r["event_day"]) else None,
            "verdict": r["verdict"], "same_day_news": int(r["same_day_news"]),
            "kind": r.get("kind", "news"), "group": r.get("group", "company"),
            "connector": r.get("connector", ""), "coverage": int(r.get("coverage", 1) or 1),
        }
        for k in num:
            v = r.get(k)
            rec[k] = None if v is None or pd.isna(v) else float(v)
        recs.append(rec)
    return recs


# AI headline scores, remembered per stock so each headline is scored only once.
AI_SCORES: dict[str, dict] = {}
AI_SCORE_CAP = 150  # most recent items scored per refresh


def _profile_and_feed(symbol: str, days: int):
    info = cached(("profile", symbol), 3600, lambda: D.get_profile(symbol))
    kp = P.load(symbol, info)
    region = "IN" if D.is_indian(symbol) else "US"
    version = (kp.get("updated") or "auto", SET.version())  # new keywords or source settings mean a fresh fetch
    items, health = cached(("feed", symbol, days, version), 150, lambda: S.fetch_all(symbol, kp, days, region))
    return info, kp, [dict(i) for i in items], health


def _score_new(symbol: str, company: str, items: list[dict]) -> None:
    known = AI_SCORES.setdefault(symbol, {})
    todo = [n for n in items[:AI_SCORE_CAP] if S._norm(n["title"]) not in known]
    if not todo:
        return
    batch = [(i, n["published"].strftime("%Y-%m-%d %H:%M"), f"{n['source']} [{n['kind']}]", n["title"], n.get("summary", ""))
             for i, n in enumerate(todo)]
    scores = A.ai_score_news(LLM, company, symbol, batch)
    for i, n in enumerate(todo):
        if i in scores:
            known[S._norm(n["title"])] = scores[i]


def _apply_scores(symbol: str, items: list[dict]) -> bool:
    known = AI_SCORES.get(symbol, {})
    used = False
    for n in items:
        sc = known.get(S._norm(n["title"]))
        if sc:
            used = True
            n["sentiment"] = sc.get("sentiment", A.keyword_sentiment(n["title"]))
            n["category"] = sc.get("category", "")
            n["importance"] = sc.get("importance")
            n["why"] = sc.get("why", "")
            n["relevant"] = sc.get("relevant", True)
        else:
            n["sentiment"] = A.keyword_sentiment(n["title"] + " " + n.get("summary", ""))
            n.setdefault("relevant", True)
    return used


def _feed_record(n: dict) -> dict:
    return {
        "id": S._norm(n["title"]), "title": n["title"], "source": n["source"], "url": n["url"],
        "published": n["published"].isoformat(), "kind": n["kind"], "group": n.get("group", "company"),
        "keyword": n.get("keyword", ""), "coverage": n.get("coverage", 1), "connectors": n.get("connectors", []),
        "sentiment": n.get("sentiment"), "category": n.get("category", ""), "why": n.get("why", ""),
        "engagement": n.get("engagement"), "summary": n.get("summary", "")[:280],
    }


def build_analysis(symbol: str, period: str, days: int, use_ai: bool) -> dict:
    daily = cached(("hist", symbol, period), 300, lambda: D.get_history(symbol, period))
    if daily.empty:
        raise HTTPException(404, f"No price data for {symbol}. Indian stocks need .NS or .BO at the end.")
    info, kp, items, health = _profile_and_feed(symbol, days)
    bsym, bname = D.benchmark_for(symbol)
    bench = cached(("hist", bsym, period), 300, lambda: D.get_history(bsym, period))
    hourly = cached(("hourly", symbol), 600, lambda: D.get_hourly(symbol))

    ai_error = None
    if use_ai and A.llm_ready(LLM) and items:
        try:
            _score_new(symbol, info["name"], items)
        except Exception as e:
            ai_error = _ai_fail("headline scoring", e)
    ai_used = _apply_scores(symbol, items)
    hidden = sum(1 for n in items if not n.get("relevant", True))
    items = [n for n in items if n.get("relevant", True)]

    # Price-reaction analysis uses news and official filings; forum posts and videos stay in the feed.
    events = A.build_event_table([n for n in items if n["kind"] in ("news", "filing")], daily, bench, hourly, symbol)
    summary = A.price_summary(daily, bench, bname, {})

    commodities = {t: (P.COMMODITIES[t][0], cached(("hist", t, period), 600, lambda t=t: D.get_history(t, period)))
                   for t in kp["commodities"]}
    tech = L.technical_lens(daily)
    risk = L.risk_lens(daily, bench)
    rel = L.relative_lens(daily, bench, bname)
    sent = L.sentiment_lens(events)
    att = L.attention_lens(daily, events)
    mat = L.materials_lens(daily, commodities, events)
    feed_tile = L.feed_tile(items, health)
    candles = [{"time": t.strftime("%Y-%m-%d"), "open": float(r.Open), "high": float(r.High),
                "low": float(r.Low), "close": float(r.Close), "volume": float(r.Volume)}
               for t, r in daily.iterrows()]
    kinds = {}
    for n in items:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1

    result = {
        "symbol": symbol, "benchmark": bname, "period": period, "days": days,
        "ai_used": ai_used, "ai_error": ai_error, "hidden_irrelevant": hidden,
        "summary": summary, "findings": A.rule_based_summary(events, summary),
        "threshold": events.attrs.get("threshold") if not events.empty else None,
        "candles": candles, "events": _events_records(events),
        "technical": tech, "risk": risk, "relative": rel, "sentiment": sent, "attention": att, "materials": mat,
        "feed": {"tile": feed_tile, "health": health, "kinds": kinds, "total": len(items)},
        "profile": kp,
        "overview": [feed_tile, L.impact_tile(events), sent["tile"], mat["tile"], tech["tile"], rel["tile"],
                     att["tile"], risk["tile"]],
    }
    with _lock:
        _cache[("context", symbol, period, days)] = (time.time(), {
            "company": info["name"],
            "summary": A.summary_text(info["name"], symbol, summary),
            "events": A.events_text(events, limit=60),
        })
    return clean(result)


@app.get("/api/analysis/{symbol}")
def analysis(symbol: str, period: str = "6mo", days: int = 30, ai: int = 0):
    if period not in ("1mo", "3mo", "6mo", "1y", "2y"):
        raise HTTPException(400, "Unknown period")
    days = max(7, min(days, 90))
    return build_analysis(symbol, period, days, bool(ai))


@app.get("/api/feed/{symbol}")
def feed(symbol: str, days: int = 30):
    """Latest items from every source. The page polls this every 2 minutes."""
    days = max(1, min(days, 90))
    info, kp, items, health = _profile_and_feed(symbol, days)
    _apply_scores(symbol, items)
    items = [n for n in items if n.get("relevant", True)]
    return clean({"items": [_feed_record(n) for n in items[:400]], "health": health,
                  "tile": L.feed_tile(items, health), "checked": time.time()})


@app.get("/api/keywords/{symbol}")
def get_keywords(symbol: str):
    info = cached(("profile", symbol), 3600, lambda: D.get_profile(symbol))
    return clean({"profile": P.load(symbol, info), "groups": P.GROUP_LABELS, "commodities": P.catalogue(),
                  "sources": S.catalogue(symbol), "officers": info.get("officers", []), "ai_enabled": A.llm_ready(LLM)})


@app.put("/api/keywords/{symbol}")
def put_keywords(symbol: str, body: dict = Body(...)):
    return clean({"profile": P.save(symbol, body)})


@app.delete("/api/keywords/{symbol}")
def reset_keywords(symbol: str):
    P.reset(symbol)
    info = cached(("profile", symbol), 3600, lambda: D.get_profile(symbol))
    return clean({"profile": P.load(symbol, info)})


def _source_settings(symbol: str = ""):
    return clean({"sources": S.catalogue(symbol), "feeds": S._load_feeds(), "default_feeds": S.default_feeds(),
                  "custom_feeds": bool(SET.load().get("feeds")), "feed_status": S.FEED_STATUS})


@app.get("/api/settings/sources")
def get_source_settings(symbol: str = ""):
    return _source_settings(symbol)


@app.put("/api/settings/sources")
def put_source_settings(body: dict = Body(...), symbol: str = ""):
    SET.save(body.get("disabled", []), body.get("feeds"))
    return _source_settings(symbol)


@app.post("/api/settings/test-feed")
def test_feed(body: dict = Body(...)):
    url = str(body.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Enter a full address starting with https://")
    return S.test_feed(url)


@app.post("/api/keywords/{symbol}/suggest")
def suggest_keywords(symbol: str, body: dict = Body(default={})):
    if not A.llm_ready(LLM):
        raise HTTPException(400, "AI is not connected.")
    info = cached(("profile", symbol), 3600, lambda: D.get_profile(symbol))
    current = P.validate(symbol, body or P.load(symbol, info))
    try:
        raw = A.ai_suggest_keywords(LLM, info, current, P.catalogue())
    except Exception as e:
        raise HTTPException(502, _ai_fail("keyword suggestion", e))
    merged = {k: (current.get(k) or []) + (raw.get(k) or []) for k in P.GROUPS + ["exclude", "commodities"]}
    merged["extra_feeds"] = current.get("extra_feeds", [])
    return clean({"profile": P.validate(symbol, merged)})


class ReportReq(BaseModel):
    symbol: str
    period: str = "6mo"
    days: int = 30


class ChatReq(ReportReq):
    question: str
    history: list[dict] = []


def _context(req: ReportReq) -> dict:
    key = ("context", req.symbol, req.period, req.days)
    if key not in _cache:
        build_analysis(req.symbol, req.period, req.days, A.llm_ready(LLM))
    return _cache[key][1]


@app.post("/api/ai/report")
def ai_report(req: ReportReq):
    if not A.llm_ready(LLM):
        raise HTTPException(400, "AI is not connected.")
    ctx = _context(req)
    try:
        md = A.ai_postmortem(LLM, ctx["company"], req.symbol, ctx["summary"], ctx["events"])
    except Exception as e:
        raise HTTPException(502, _ai_fail("report", e))
    return {"markdown": md, "generated": datetime.now().strftime("%d %b %Y, %H:%M")}


@app.post("/api/ai/chat")
def ai_chat(req: ChatReq):
    if not A.llm_ready(LLM):
        raise HTTPException(400, "AI is not connected.")
    ctx = _context(req)
    history = [m for m in req.history if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
    try:
        answer = A.ai_chat(LLM, ctx["summary"] + "\n\nHEADLINES\n" + ctx["events"], history, req.question)
    except Exception as e:
        raise HTTPException(502, _ai_fail("chat", e))
    return {"answer": answer}


# --------------------------------------------------------------------------
# Surveillance: material price movement monitoring
# --------------------------------------------------------------------------
@app.on_event("startup")
def _start_monitor():
    SV.start()


@app.get("/api/surveillance/status")
def sv_status():
    return clean(SV.status())


@app.put("/api/surveillance/config")
def sv_config(body: dict = Body(...)):
    SV.save_config(body)
    return clean(SV.status())


@app.post("/api/surveillance/investigate")
def sv_investigate(body: dict = Body(...)):
    sym = str(body.get("symbol", "")).strip().upper()
    if not sym:
        raise HTTPException(400, "Symbol required")
    try:
        return {"id": SV.investigate_now(sym)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/surveillance/incidents")
def sv_incidents(symbol: str = ""):
    return clean(SV.list_incidents(symbol))


@app.get("/api/surveillance/incidents/{inc_id}")
def sv_incident(inc_id: str):
    inc = SV.get_incident(inc_id)
    if not inc:
        raise HTTPException(404, "Incident not found")
    return clean(inc)


@app.get("/api/surveillance/incidents/{inc_id}/report.html")
def sv_report(inc_id: str):
    inc = SV.get_incident(inc_id)
    if not inc or inc.get("status") != "complete":
        raise HTTPException(404, "Report not ready")
    return HTMLResponse(SV.report_html(inc, full=True), headers={
        "Content-Disposition": f'attachment; filename="{inc["symbol"]}_movement_report.html"'})


@app.post("/api/surveillance/incidents/{inc_id}/email")
def sv_email(inc_id: str, body: dict = Body(default={})):
    inc = SV.get_incident(inc_id)
    if not inc or inc.get("status") != "complete":
        raise HTTPException(404, "Report not ready")
    to = [e.strip() for e in (body.get("to") or SV.load_config()["recipients"]) if "@" in str(e)]
    ok, msg = SV.email_report(inc, to)
    if not ok:
        raise HTTPException(400, msg)
    return {"message": msg}


@app.post("/api/surveillance/test-email")
def sv_test_email(body: dict = Body(default={})):
    to = body.get("to") or SV.load_config()["recipients"]
    ok, msg = N.send(to, f"[{AI_NAME}] Test email from Drishti surveillance",
                     "<p style='font-family:Arial'>Email alerts are working. You will receive material price movement alerts and reports at this address.</p>")
    if not ok:
        raise HTTPException(400, msg)
    return {"message": msg}


app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "127.0.0.1")  # set HOST=0.0.0.0 on a server
    if host == "127.0.0.1":
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    uvicorn.run(app, host=host, port=port)
