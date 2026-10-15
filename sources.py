"""Multi-source news engine.

Every connector returns items in one shape:
  title, source (outlet), url, published (UTC Timestamp), summary,
  kind ('news' | 'filing' | 'forum' | 'video'), connector (id), group (keyword group), keyword
Connectors run in parallel; each reports its own health so one failure never blocks the rest.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

import feedparser
import pandas as pd
import requests

import data as D
from profiles import GROUPS

BASE = Path(__file__).parent
UA = D.UA
TIMEOUT = 12

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
_cache: dict = {}
_lock = threading.Lock()


def _cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


_TAG = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", text or ""))).strip()


def _from_struct(st):
    return pd.Timestamp(datetime(*st[:6], tzinfo=timezone.utc)) if st else None


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _item(title, source, url, published, summary="", kind="news", connector="", group="company", keyword="", **extra):
    return {"title": _clean(title)[:300], "source": source or _domain(url) or connector, "url": url,
            "published": published, "summary": _clean(summary)[:500], "kind": kind,
            "connector": connector, "group": group, "keyword": keyword, **extra}


def _get(url, **kw):
    headers = {**UA, **kw.pop("headers", {})}
    r = requests.get(url, headers=headers, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def _quoted(t: str) -> str:
    return f'"{t}"' if re.search(r"\s", t) else t


def _or(terms, n=6) -> str:
    return " OR ".join(_quoted(t) for t in terms[:n])


# --------------------------------------------------------------------------
# Keyword matching
# --------------------------------------------------------------------------
def _rx(term: str):
    # All-caps tickers and abbreviations (RELIANCE, RIL, TCS) match case exactly, so that
    # "RELIANCE" does not catch every "Reliance Power" or "Reliance Capital" headline.
    flags = 0 if term.isupper() else re.I
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", flags)


class Matcher:
    def __init__(self, profile: dict):
        self.rules = [(g, t, _rx(t)) for g in GROUPS for t in profile.get(g, [])]
        self.excl = [_rx(t) for t in profile.get("exclude", [])]

    def match(self, text: str, groups=None):
        if any(x.search(text) for x in self.excl):
            return None
        for g, t, rx in self.rules:
            if (groups is None or g in groups) and rx.search(text):
                return g, t
        return None


# --------------------------------------------------------------------------
# Connectors
# --------------------------------------------------------------------------
def c_google_news(ctx):
    loc = "hl=en-IN&gl=IN&ceid=IN:en" if ctx["region"] == "IN" else "hl=en-US&gl=US&ceid=US:en"
    out = []
    excl = " ".join(f"-{_quoted(t)}" for t in ctx["profile"].get("exclude", [])[:5])
    for g in GROUPS:
        terms = ctx["profile"].get(g, [])
        if not terms:
            continue
        q = f"({_or(terms)}) {excl} when:{ctx['days']}d".strip()
        feed = feedparser.parse(_get(f"https://news.google.com/rss/search?q={quote_plus(q)}&{loc}").content)
        for e in feed.entries[:60]:
            src = (e.get("source") or {}).get("title") or ""
            title = e.get("title", "")
            if src and title.endswith(" - " + src):
                title = title[: -(len(src) + 3)]
            out.append(_item(title, src, e.get("link"), _from_struct(e.get("published_parsed")),
                             connector="google_news", group=g, keyword=terms[0]))
    return out


def c_bing_news(ctx):
    out = []
    for g in ("company", "people"):
        terms = ctx["profile"].get(g, [])
        if not terms:
            continue
        url = f"https://www.bing.com/news/search?q={quote_plus(_or(terms, 4))}&format=rss&count=50"
        feed = feedparser.parse(_get(url).content)
        for e in feed.entries:
            link = e.get("link", "")
            real = parse_qs(urlparse(link).query).get("url", [link])[0]
            out.append(_item(e.get("title", ""), e.get("news_source") or _domain(real), real,
                             _from_struct(e.get("published_parsed")), e.get("summary", ""),
                             connector="bing_news", group=g, keyword=terms[0]))
    return out


def _load_feeds():
    p = BASE / "feeds.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"IN": [], "GLOBAL": []}


def _fetch_feed(url):
    def go():
        return feedparser.parse(_get(url).content).entries
    return _cached(("feed", url), 120, go)  # shared by every stock, refreshed every 2 minutes


def c_publishers(ctx):
    feeds = _load_feeds().get("IN" if ctx["region"] == "IN" else "GLOBAL", [])
    out, errors = [], 0
    m: Matcher = ctx["matcher"]

    def one(entry):
        name, url = entry
        try:
            return name, _fetch_feed(url)
        except Exception:
            return name, None

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(one, feeds))
    for name, entries in results:
        if entries is None:
            errors += 1
            continue
        for e in entries:
            text = f"{e.get('title', '')} {e.get('summary', '')}"
            hit = m.match(text)
            if hit:
                out.append(_item(e.get("title", ""), name.split(" ")[0] if " " in name else name, e.get("link"),
                                 _from_struct(e.get("published_parsed") or e.get("updated_parsed")),
                                 e.get("summary", ""), connector="publishers", group=hit[0], keyword=hit[1]))
    ctx["notes"]["publishers"] = f"{len(feeds) - errors} of {len(feeds)} feeds reachable"
    if errors == len(feeds) and feeds:
        raise RuntimeError("No publisher feeds reachable")
    return out


def c_yahoo(ctx):
    return [_item(n["title"], n["source"], n["url"], n["published"], n.get("summary", ""), connector="yahoo")
            for n in D._yahoo_news(ctx["symbol"])]


def c_gdelt(ctx):
    terms = [t for t in ctx["profile"]["company"] + ctx["profile"].get("people", []) if len(t) >= 4][:6]
    if not terms:
        return []
    q = _or(terms, 6)
    q = (f"({q})" if len(terms) > 1 else q) + " sourcelang:english"
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=min(ctx["days"], 90))
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?mode=ArtList&format=json&sort=DateDesc&maxrecords=150"
           f"&query={quote_plus(q)}&startdatetime={start:%Y%m%d%H%M%S}&enddatetime={end:%Y%m%d%H%M%S}")
    r = _get(url)
    try:
        arts = r.json().get("articles", [])
    except ValueError:
        raise RuntimeError(r.text[:120])
    out = []
    for a in arts:
        ts = pd.to_datetime(a.get("seendate"), format="%Y%m%dT%H%M%SZ", utc=True, errors="coerce")
        out.append(_item(a.get("title", ""), a.get("domain"), a.get("url"), None if pd.isna(ts) else ts,
                         connector="gdelt", group="company", keyword=terms[0]))
    return out


def c_nse_filings(ctx):
    sym = ctx["symbol"].upper()
    if not sym.endswith(".NS"):
        return []
    base = sym[:-3]
    s = requests.Session()
    s.headers.update({**UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
                      "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"})
    s.get("https://www.nseindia.com", timeout=TIMEOUT)  # sets the cookies NSE requires
    r = s.get(f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={quote_plus(base)}",
              timeout=TIMEOUT)
    r.raise_for_status()
    rows = r.json()
    rows = rows.get("data", []) if isinstance(rows, dict) else rows
    out = []
    for row in rows[:80]:
        raw = row.get("sort_date") or row.get("an_dt")
        ts = pd.to_datetime(raw, errors="coerce")
        if ts is None or pd.isna(ts):
            continue
        ts = ts.tz_localize("Asia/Kolkata").tz_convert("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        desc = row.get("desc") or "Announcement"
        detail = _clean(row.get("attchmntText") or "")
        title = f"{desc}: {detail[:160]}" if detail else desc
        out.append(_item(title, "NSE filing", row.get("attchmntFile") or "https://www.nseindia.com", ts,
                         detail, kind="filing", connector="nse_filings", group="company", keyword=base))
    return out


_reddit_token = {"value": None, "exp": 0}


def _reddit_auth():
    cid, sec = os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET")
    if not (cid and sec):
        return None
    if _reddit_token["value"] and time.time() < _reddit_token["exp"]:
        return _reddit_token["value"]
    r = requests.post("https://www.reddit.com/api/v1/access_token", auth=(cid, sec),
                      data={"grant_type": "client_credentials"},
                      headers={"User-Agent": "headline-impact/1.0"}, timeout=TIMEOUT)
    r.raise_for_status()
    tok = r.json()["access_token"]
    _reddit_token.update(value=tok, exp=time.time() + 3000)
    return tok


def c_reddit(ctx):
    terms = ctx["profile"]["company"][:3] + ctx["profile"].get("people", [])[:2]
    t = "week" if ctx["days"] <= 7 else "month" if ctx["days"] <= 31 else "year"
    params = f"q={quote_plus(_or(terms, 5))}&sort=new&t={t}&limit=75&type=link"
    tok = _reddit_auth()
    if tok:
        r = requests.get(f"https://oauth.reddit.com/search?{params}", timeout=TIMEOUT,
                         headers={"Authorization": f"bearer {tok}", "User-Agent": "headline-impact/1.0"})
    else:
        r = requests.get(f"https://www.reddit.com/search.json?{params}", timeout=TIMEOUT,
                         headers={"User-Agent": "headline-impact/1.0 (news research tool)"})
    r.raise_for_status()
    m: Matcher = ctx["matcher"]
    out = []
    for ch in r.json().get("data", {}).get("children", []):
        d = ch.get("data", {})
        if d.get("over_18"):
            continue
        text = f"{d.get('title', '')} {d.get('selftext', '')}"
        hit = m.match(text, groups=("company", "people", "products"))
        if not hit:
            continue
        out.append(_item(d.get("title", ""), d.get("subreddit_name_prefixed", "Reddit"),
                         "https://www.reddit.com" + d.get("permalink", ""),
                         pd.Timestamp(d.get("created_utc", 0), unit="s", tz="UTC"), d.get("selftext", ""),
                         kind="forum", connector="reddit", group=hit[0], keyword=hit[1],
                         engagement=int(d.get("score", 0)) + int(d.get("num_comments", 0))))
    return out


def c_youtube(ctx):
    key = os.getenv("YOUTUBE_API_KEY")
    terms = ctx["profile"]["company"][:2] + ctx["profile"].get("people", [])[:1]
    after = (datetime.now(timezone.utc) - timedelta(days=ctx["days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    region = "&regionCode=IN" if ctx["region"] == "IN" else ""
    url = ("https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&order=date&maxResults=25"
           f"&relevanceLanguage=en{region}&publishedAfter={after}&q={quote_plus(_or(terms, 3))}&key={key}")
    out = []
    for it in _get(url).json().get("items", []):
        sn = it.get("snippet", {})
        vid = it.get("id", {}).get("videoId")
        if not vid:
            continue
        out.append(_item(sn.get("title", ""), sn.get("channelTitle", "YouTube"), f"https://www.youtube.com/watch?v={vid}",
                         pd.to_datetime(sn.get("publishedAt"), utc=True, errors="coerce"), sn.get("description", ""),
                         kind="video", connector="youtube", group="company", keyword=terms[0]))
    return out


def c_newsapi(ctx):
    key = os.getenv("NEWSAPI_KEY")
    terms = ctx["profile"]["company"][:3] + ctx["profile"].get("people", [])[:2]
    frm = (datetime.now(timezone.utc) - timedelta(days=min(ctx["days"], 29))).strftime("%Y-%m-%d")
    url = (f"https://newsapi.org/v2/everything?q={quote_plus(_or(terms, 5))}&from={frm}"
           f"&sortBy=publishedAt&language=en&pageSize=100&apiKey={key}")
    out = []
    for a in _get(url).json().get("articles", []):
        out.append(_item(a.get("title", ""), (a.get("source") or {}).get("name"), a.get("url"),
                         pd.to_datetime(a.get("publishedAt"), utc=True, errors="coerce"), a.get("description", ""),
                         connector="newsapi", group="company", keyword=terms[0]))
    return out


def c_finnhub(ctx):
    key = os.getenv("FINNHUB_KEY")
    sym = ctx["symbol"].upper()
    if "." in sym or "^" in sym:
        return []  # Finnhub company news covers US listings
    to = datetime.now(timezone.utc).date()
    frm = to - timedelta(days=ctx["days"])
    url = f"https://finnhub.io/api/v1/company-news?symbol={sym}&from={frm}&to={to}&token={key}"
    return [_item(a.get("headline", ""), a.get("source"), a.get("url"), pd.Timestamp(a.get("datetime", 0), unit="s", tz="UTC"),
                  a.get("summary", ""), connector="finnhub", group="company", keyword=sym)
            for a in _get(url).json()[:100]]


def c_custom(ctx):
    out = []
    for url in ctx["profile"].get("extra_feeds", []):
        feed = feedparser.parse(_get(url).content)
        name = feed.feed.get("title") or _domain(url)
        for e in feed.entries[:50]:
            out.append(_item(e.get("title", ""), name, e.get("link"),
                             _from_struct(e.get("published_parsed") or e.get("updated_parsed")), e.get("summary", ""),
                             connector="custom_feeds", group="company", keyword="custom feed"))
    return out


CONNECTORS = [
    {"id": "google_news", "name": "Google News", "kind": "news", "fn": c_google_news, "env": None,
     "desc": "Thousands of publishers, searched once per keyword group."},
    {"id": "bing_news", "name": "Bing News", "kind": "news", "fn": c_bing_news, "env": None,
     "desc": "Second aggregator that often catches stories Google misses."},
    {"id": "publishers", "name": "Publisher RSS feeds", "kind": "news", "fn": c_publishers, "env": None,
     "desc": "Live feeds from ET, Moneycontrol, Mint, Business Standard, BusinessLine and more (edit feeds.json)."},
    {"id": "yahoo", "name": "Yahoo Finance", "kind": "news", "fn": c_yahoo, "env": None,
     "desc": "Ticker news from Yahoo Finance."},
    {"id": "gdelt", "name": "GDELT global media", "kind": "news", "fn": c_gdelt, "env": None,
     "desc": "Monitors world online news in near real time, updated every 15 minutes."},
    {"id": "nse_filings", "name": "NSE announcements", "kind": "filing", "fn": c_nse_filings, "env": None,
     "only": "NSE", "desc": "Official exchange filings: results, board meetings, orders, management changes."},
    {"id": "reddit", "name": "Reddit", "kind": "forum", "fn": c_reddit, "env": None,
     "desc": "Investor discussions. Add REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET for reliable access on servers."},
    {"id": "youtube", "name": "YouTube", "kind": "video", "fn": c_youtube, "env": "YOUTUBE_API_KEY",
     "desc": "Business channels and video coverage. Free key from Google Cloud."},
    {"id": "newsapi", "name": "NewsAPI.org", "kind": "news", "fn": c_newsapi, "env": "NEWSAPI_KEY",
     "desc": "80,000+ news sources. Free developer key, paid plan needed for commercial use."},
    {"id": "finnhub", "name": "Finnhub", "kind": "news", "fn": c_finnhub, "env": "FINNHUB_KEY",
     "only": "US", "desc": "Company news for US-listed stocks. Free key."},
    {"id": "custom_feeds", "name": "Your custom feeds", "kind": "news", "fn": c_custom, "env": None,
     "desc": "Company press-release or investor-relations RSS feeds you add on this page."},
]


def _disabled():
    return {x.strip() for x in os.getenv("DISABLED_SOURCES", "").split(",") if x.strip()}


def catalogue(symbol: str = "") -> list[dict]:
    out = []
    for c in CONNECTORS:
        state, note = _availability(c, symbol)
        out.append({"id": c["id"], "name": c["name"], "kind": c["kind"], "desc": c["desc"], "state": state, "note": note})
    return out


def _availability(c, symbol):
    sym = symbol.upper()
    if c["id"] in _disabled():
        return "off", "Disabled with DISABLED_SOURCES"
    if c["env"] and not os.getenv(c["env"]):
        return "off", f"Add {c['env']} in your environment settings to switch on"
    if c.get("only") == "NSE" and sym and not sym.endswith(".NS"):
        return "off", "Only for NSE-listed stocks (.NS)"
    if c.get("only") == "US" and sym and ("." in sym or "^" in sym):
        return "off", "Only for US-listed stocks"
    return "on", ""


# --------------------------------------------------------------------------
# Run everything
# --------------------------------------------------------------------------
def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", t.lower())[:70]


def fetch_all(symbol: str, profile: dict, days: int, region: str):
    ctx = {"symbol": symbol, "profile": profile, "days": days, "region": region,
           "matcher": Matcher(profile), "notes": {}}
    if not profile.get("extra_feeds"):
        skip_custom = True
    else:
        skip_custom = False

    def run(c):
        state, note = _availability(c, symbol)
        if c["id"] == "custom_feeds" and skip_custom:
            return c, [], "off", "No custom feeds added", 0
        if state == "off":
            return c, [], "off", note, 0
        t0 = time.time()
        try:
            items = c["fn"](ctx) or []
            return c, items, ("ok" if items else "empty"), ctx["notes"].get(c["id"], ""), int((time.time() - t0) * 1000)
        except Exception as e:
            msg = str(e)
            if "403" in msg or "429" in msg:
                msg = "Blocked or rate-limited by the source (" + msg[:60] + ")"
            return c, [], "error", msg[:180], int((time.time() - t0) * 1000)

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(run, CONNECTORS))

    cutoff = pd.Timestamp(datetime.now(timezone.utc) - timedelta(days=days))
    future = pd.Timestamp(datetime.now(timezone.utc) + timedelta(hours=1))
    matcher = ctx["matcher"]
    merged: dict[str, dict] = {}
    health = []
    for c, items, status, note, ms in results:
        kept, newest = 0, None
        for it in items:
            ts = it.get("published")
            if ts is None or pd.isna(ts) or ts < cutoff or ts > future or not it.get("title"):
                continue
            # Prefer the most specific group the headline mentions (company beats product beats material).
            hit = matcher.match(it["title"] + " " + it.get("summary", ""))
            if hit:
                it["group"], it["keyword"] = hit
            key = _norm(it["title"])
            kept += 1
            newest = ts if newest is None or ts > newest else newest
            if key in merged:
                m = merged[key]
                m["outlets"].add(it["source"])
                m["connectors"].add(c["id"])
                if ts < m["published"]:  # keep the earliest time anyone published it
                    m["published"] = ts
                if m["kind"] == "news" and it["kind"] == "filing":
                    m["kind"] = "filing"
            else:
                it["outlets"] = {it["source"]}
                it["connectors"] = {c["id"]}
                merged[key] = it
        health.append({"id": c["id"], "name": c["name"], "kind": c["kind"], "status": status, "note": note,
                       "count": kept, "newest": newest.isoformat() if newest is not None else None, "ms": ms,
                       "desc": c["desc"]})

    items = sorted(merged.values(), key=lambda x: x["published"], reverse=True)
    for it in items:
        it["coverage"] = len(it.pop("outlets"))
        it["connectors"] = sorted(it["connectors"])
        it["connector"] = it["connectors"][0]
    return items, health
