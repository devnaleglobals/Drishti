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
import settings as SET
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


def _search_terms(terms):
    """Terms safe to send to outside search engines.
    Those engines ignore case, so a ticker like RELIANCE becomes the word "reliance" and matches anything.
    Single all-caps words (tickers, abbreviations) are dropped whenever a fuller name is available."""
    terms = [t for t in terms if t and t.strip()]
    full = [t for t in terms if not (t.isupper() and " " not in t.strip())]
    return full or terms


def _or(terms, n=6) -> str:
    return " OR ".join(_quoted(t) for t in _search_terms(terms)[:n])


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
        market = "&setlang=en-IN&cc=IN" if ctx["region"] == "IN" else "&setlang=en-US&cc=US"
        url = f"https://www.bing.com/news/search?q={quote_plus(_or(terms, 4))}&format=rss&count=50{market}"
        r = _get(url, headers={"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.5",
                               "Accept-Language": "en-IN,en;q=0.9"})
        head = r.text[:600].lower()
        if "<rss" not in head and "<feed" not in head:
            raise RuntimeError("Bing sent a web page instead of a news feed. It is likely blocking server traffic.")
        feed = feedparser.parse(r.content)
        for e in feed.entries:
            link = e.get("link", "")
            real = parse_qs(urlparse(link).query).get("url", [link])[0]
            out.append(_item(e.get("title", ""), e.get("news_source") or _domain(real), real,
                             _from_struct(e.get("published_parsed")), e.get("summary", ""),
                             connector="bing_news", group=g, keyword=terms[0]))
    return out


def default_feeds():
    try:
        data = json.loads((BASE / "feeds.json").read_text(encoding="utf-8"))
        return {"IN": data.get("IN", []), "GLOBAL": data.get("GLOBAL", [])}
    except Exception:
        return {"IN": [], "GLOBAL": []}


def _load_feeds():
    """Feeds edited in the app win over feeds.json."""
    return SET.load().get("feeds") or default_feeds()


FEED_STATUS: dict[str, dict] = {}  # url -> last check result, shown in the settings panel


def _fetch_feed(url):
    def go():
        try:
            r = _get(url, headers={"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.5"})
            parsed = feedparser.parse(r.content)
            if not parsed.entries and parsed.bozo:
                raise RuntimeError("Not a valid RSS feed")
            newest = max((_from_struct(e.get("published_parsed") or e.get("updated_parsed")) for e in parsed.entries
                          if e.get("published_parsed") or e.get("updated_parsed")), default=None)
            FEED_STATUS[url] = {"ok": True, "entries": len(parsed.entries), "error": "",
                                "newest": newest.isoformat() if newest is not None else None,
                                "checked": datetime.now(timezone.utc).isoformat()}
            return parsed.entries
        except Exception as e:
            FEED_STATUS[url] = {"ok": False, "entries": 0, "error": str(e)[:140], "newest": None,
                                "checked": datetime.now(timezone.utc).isoformat()}
            raise
    return _cached(("feed", url), 120, go)  # shared by every stock, refreshed every 2 minutes


def test_feed(url: str) -> dict:
    """Used by the 'Test' button when adding a feed."""
    try:
        r = _get(url, headers={"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.5"})
        parsed = feedparser.parse(r.content)
        if not parsed.entries:
            return {"ok": False, "error": "No articles found. Check that this is an RSS or Atom feed address."}
        newest = max((_from_struct(e.get("published_parsed") or e.get("updated_parsed")) for e in parsed.entries
                      if e.get("published_parsed") or e.get("updated_parsed")), default=None)
        return {"ok": True, "title": parsed.feed.get("title", ""), "entries": len(parsed.entries),
                "newest": newest.isoformat() if newest is not None else None,
                "sample": [e.get("title", "") for e in parsed.entries[:3]]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


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
    failed = [name for name, entries in results if entries is None]
    note = f"{len(feeds) - errors} of {len(feeds)} feeds reachable"
    if failed:
        note += ". Not reachable: " + ", ".join(failed[:4])
    ctx["notes"]["publishers"] = note
    if errors == len(feeds) and feeds:
        raise RuntimeError("No publisher feeds reachable")
    return out


def c_yahoo(ctx):
    items = [_item(n["title"], n["source"], n["url"], n["published"], n.get("summary", ""), connector="yahoo")
             for n in D._yahoo_news(ctx["symbol"])]
    if items:
        return items
    # yfinance often returns nothing on cloud servers; Yahoo's per-ticker RSS feed is a separate route.
    region = "region=IN&lang=en-IN" if ctx["region"] == "IN" else "region=US&lang=en-US"
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote_plus(ctx['symbol'])}&{region}"
    r = _get(url, headers={"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.5"})
    feed = feedparser.parse(r.content)
    ctx["notes"]["yahoo"] = "Using Yahoo's RSS feed (the yfinance news call returned nothing)"
    return [_item(e.get("title", ""), "Yahoo Finance", e.get("link"), _from_struct(e.get("published_parsed")),
                  e.get("summary", ""), connector="yahoo") for e in feed.entries]


def c_gdelt(ctx):
    terms = [t for t in _search_terms(ctx["profile"]["company"]) + ctx["profile"].get("people", []) if len(t) >= 4][:6]
    if not terms:
        return []
    q = _or(terms, 6)
    q = (f"({q})" if len(terms) > 1 else q) + " sourcelang:english"
    key = (q, ctx["days"])
    G = _GDELT
    with G["lock"]:  # GDELT allows one request every 5 seconds per server, so calls queue up here
        now = time.time()
        good = G["good"].get(key)
        if good and now - good[0] < 900:  # GDELT itself updates every 15 minutes
            ctx["notes"]["gdelt"] = f"Cached, refreshed {int((now - good[0]) // 60)} min ago"
            return good[1]
        if now < G["paused_until"]:
            if good:
                ctx["notes"]["gdelt"] = f"Rate-limited; showing results from {int((now - good[0]) // 60)} min ago"
                return good[1]
            raise RuntimeError(f"Rate-limited by GDELT. Retrying automatically after "
                               f"{datetime.fromtimestamp(G['paused_until'], timezone.utc):%H:%M} UTC.")
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=min(ctx["days"], 90))
        url = ("https://api.gdeltproject.org/api/v2/doc/doc?mode=ArtList&format=json&sort=DateDesc&maxrecords=150"
               f"&query={quote_plus(q)}&startdatetime={start:%Y%m%d%H%M%S}&enddatetime={end:%Y%m%d%H%M%S}")
        r = None
        for attempt in range(2):
            wait = 6 - (time.time() - G["last_call"])
            if wait > 0:
                time.sleep(wait)
            G["last_call"] = time.time()
            r = requests.get(url, headers=UA, timeout=TIMEOUT)
            if r.status_code != 429:
                break
            time.sleep(6)
        if r.status_code == 429:
            G["paused_until"] = time.time() + 600
            if good:
                ctx["notes"]["gdelt"] = f"Rate-limited; showing results from {int((time.time() - good[0]) // 60)} min ago"
                return good[1]
            raise RuntimeError("Rate-limited by GDELT (it allows one request every 5 seconds and Render's servers are shared). "
                               "Retrying automatically in 10 minutes.")
        r.raise_for_status()
    try:
        arts = r.json().get("articles", [])
    except ValueError:
        raise RuntimeError("GDELT replied: " + r.text[:120])
    out = []
    for a in arts:
        ts = pd.to_datetime(a.get("seendate"), format="%Y%m%dT%H%M%SZ", utc=True, errors="coerce")
        out.append(_item(a.get("title", ""), a.get("domain"), a.get("url"), None if pd.isna(ts) else ts,
                         connector="gdelt", group="company", keyword=terms[0]))
    _GDELT["good"][key] = (time.time(), out)
    return out


_GDELT = {"lock": threading.Lock(), "last_call": 0.0, "paused_until": 0.0, "good": {}}


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


def c_bse_filings(ctx):
    code = str(ctx["profile"].get("bse_code") or "").strip()
    if not code:
        return []
    end = datetime.now(timezone.utc) + timedelta(hours=6)
    start = end - timedelta(days=min(ctx["days"], 60))
    url = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno=1&strCat=-1&strSearch=P&strType=C"
           f"&subcategory=-1&strScrip={code}&strPrevDate={start:%Y%m%d}&strToDate={end:%Y%m%d}")
    r = _get(url, headers={"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com",
                           "Accept": "application/json, text/plain, */*"})
    rows = r.json().get("Table", [])
    out = []
    for row in rows[:80]:
        ts = pd.to_datetime(row.get("DT_TM") or row.get("NEWS_DT"), errors="coerce")
        if ts is None or pd.isna(ts):
            continue
        ts = ts.tz_localize("Asia/Kolkata").tz_convert("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        title = row.get("NEWSSUB") or row.get("HEADLINE") or "BSE announcement"
        att = row.get("ATTACHMENTNAME")
        link = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}" if att else "https://www.bseindia.com/corporates/ann.html"
        out.append(_item(title, "BSE filing", link, ts, row.get("HEADLINE") or "", kind="filing",
                         connector="bse_filings", group="company", keyword=code))
    return out


def c_sebi(ctx):
    feed = feedparser.parse(_get("https://www.sebi.gov.in/sebirss.xml").content)
    m: Matcher = ctx["matcher"]
    out = []
    for e in feed.entries:
        hit = m.match(f"{e.get('title', '')} {e.get('summary', '')}", groups=("company", "people", "products"))
        if hit:
            out.append(_item(e.get("title", ""), "SEBI", e.get("link"), _from_struct(e.get("published_parsed")),
                             e.get("summary", ""), kind="filing", connector="sebi", group=hit[0], keyword=hit[1]))
    return out


def c_valuepickr(ctx):
    term = ctx["profile"]["company"][0]
    r = _get(f"https://forum.valuepickr.com/search.json?q={quote_plus(term + ' order:latest')}",
             headers={"Accept": "application/json"})
    data = r.json()
    topics = {t["id"]: t for t in data.get("topics", [])}
    out = []
    for p in data.get("posts", [])[:50]:
        t = topics.get(p.get("topic_id"), {})
        title = t.get("title", "ValuePickr post")
        text = _clean(p.get("blurb", ""))
        out.append(_item(f"{title}: {text[:160]}", "ValuePickr forum",
                         f"https://forum.valuepickr.com/t/{t.get('slug', 'topic')}/{p.get('topic_id')}/{p.get('post_number', 1)}",
                         pd.to_datetime(p.get("created_at"), utc=True, errors="coerce"), text, kind="forum",
                         connector="valuepickr", group="company", keyword=term,
                         engagement=int(p.get("like_count") or 0)))
    return out


def c_x(ctx):
    token = os.getenv("X_BEARER_TOKEN")
    terms = ctx["profile"]["company"][:3] + ctx["profile"].get("people", [])[:1]
    q = f"({_or(terms, 4)}) -is:retweet lang:en"
    url = ("https://api.x.com/2/tweets/search/recent?max_results=50&tweet.fields=created_at,public_metrics"
           f"&expansions=author_id&user.fields=username,public_metrics&query={quote_plus(q)}")
    data = _get(url, headers={"Authorization": f"Bearer {token}"}).json()
    users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
    out = []
    for t in data.get("data", []):
        u = users.get(t.get("author_id"), {})
        pm = t.get("public_metrics", {})
        eng = sum(int(pm.get(k, 0)) for k in ("like_count", "retweet_count", "reply_count", "quote_count"))
        out.append(_item(t.get("text", "")[:220], "@" + u.get("username", "x"),
                         f"https://x.com/{u.get('username', 'i')}/status/{t.get('id')}",
                         pd.to_datetime(t.get("created_at"), utc=True, errors="coerce"), t.get("text", ""),
                         kind="social", connector="x", group="company", keyword=terms[0], engagement=eng,
                         reach=int(u.get("public_metrics", {}).get("followers_count", 0))))
    return out


CONNECTORS = [
    {"id": "google_news", "name": "Google News", "kind": "news", "fn": c_google_news, "env": None,
     "desc": "Thousands of publishers, searched once per keyword group."},
    {"id": "bing_news", "name": "Bing News", "kind": "news", "fn": c_bing_news, "env": None,
     "desc": "Second aggregator that often catches stories Google misses."},
    {"id": "publishers", "name": "Publisher RSS feeds", "kind": "news", "fn": c_publishers, "env": None,
     "desc": "Live feeds from ET, Moneycontrol, Mint, Business Standard, BusinessLine and more. Manage them below."},
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
    {"id": "bse_filings", "name": "BSE announcements", "kind": "filing", "fn": c_bse_filings, "env": None,
     "needs_bse": True, "desc": "Official BSE filings. Add the company's 6-digit BSE scrip code on this page."},
    {"id": "sebi", "name": "SEBI updates", "kind": "filing", "fn": c_sebi, "env": None,
     "desc": "SEBI orders, circulars and press releases that mention this company."},
    {"id": "valuepickr", "name": "ValuePickr forum", "kind": "forum", "fn": c_valuepickr, "env": None,
     "desc": "India's largest public stock-discussion forum."},
    {"id": "x", "name": "X (Twitter)", "kind": "social", "fn": c_x, "env": "X_BEARER_TOKEN",
     "desc": "Posts on X. Needs a paid X API plan; add X_BEARER_TOKEN."},
    {"id": "custom_feeds", "name": "Your custom feeds", "kind": "news", "fn": c_custom, "env": None,
     "desc": "Company press-release or investor-relations RSS feeds you add on this page."},
]


def _env_disabled():
    return {x.strip() for x in os.getenv("DISABLED_SOURCES", "").split(",") if x.strip()}


def _app_disabled():
    return set(SET.load().get("disabled") or [])


def catalogue(symbol: str = "") -> list[dict]:
    out = []
    env_off, app_off = _env_disabled(), _app_disabled()
    for c in CONNECTORS:
        state, note = _availability(c, symbol)
        locked = ""
        if c["id"] in env_off:
            locked = "Switched off in the server environment (DISABLED_SOURCES)"
        elif c["env"] and not os.getenv(c["env"]):
            locked = f"Needs {c['env']} in the server environment"
        out.append({"id": c["id"], "name": c["name"], "kind": c["kind"], "desc": c["desc"], "state": state,
                    "note": note, "enabled": c["id"] not in app_off, "locked": locked})
    return out


def _availability(c, symbol):
    sym = symbol.upper()
    if c["id"] in _env_disabled():
        return "off", "Disabled with DISABLED_SOURCES"
    if c["id"] in _app_disabled():
        return "off", "Switched off in settings"
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
TRUSTED = {"yahoo", "nse_filings", "bse_filings", "finnhub", "custom_feeds"}


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
        if c.get("needs_bse") and not str(profile.get("bse_code") or "").strip():
            return c, [], "off", "Add the BSE scrip code on the Keywords and sources lens", 0
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
        kept, newest, dropped = 0, None, 0
        for it in items:
            ts = it.get("published")
            if ts is None or pd.isna(ts) or ts < cutoff or ts > future or not it.get("title"):
                continue
            # Relevance gate: results from search engines must actually mention one of the keywords.
            # Company-specific feeds (exchange filings, ticker news, custom feeds) are trusted as they are.
            hit = matcher.match(it["title"] + " " + it.get("summary", ""))
            if hit:
                it["group"], it["keyword"] = hit
            elif c["id"] not in TRUSTED:
                dropped += 1
                continue
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
        if dropped:
            note = (note + ". " if note else "") + f"{dropped} unrelated result(s) removed"
        health.append({"id": c["id"], "name": c["name"], "kind": c["kind"], "status": status, "note": note,
                       "count": kept, "newest": newest.isoformat() if newest is not None else None, "ms": ms,
                       "desc": c["desc"]})

    items = sorted(merged.values(), key=lambda x: x["published"], reverse=True)
    for it in items:
        it["coverage"] = len(it.pop("outlets"))
        it["connectors"] = sorted(it["connectors"])
        it["connector"] = it["connectors"][0]
    return items, health
