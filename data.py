"""Data layer: live quotes, price history, symbol search and news for a stock."""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import requests
import yfinance as yf

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# --------------------------------------------------------------------------
# Market helpers
# --------------------------------------------------------------------------
_BENCHMARKS = {
    ".NS": ("^NSEI", "Nifty 50"),
    ".BO": ("^BSESN", "Sensex"),
    ".L": ("^FTSE", "FTSE 100"),
    ".T": ("^N225", "Nikkei 225"),
    ".HK": ("^HSI", "Hang Seng"),
}
_MARKET_CLOSE = {".NS": (15, 30), ".BO": (15, 30), ".L": (16, 30), ".T": (15, 0), ".HK": (16, 0)}


def is_indian(symbol: str) -> bool:
    return symbol.upper().endswith((".NS", ".BO"))


def benchmark_for(symbol: str) -> tuple[str, str]:
    s = symbol.upper()
    for suffix, bench in _BENCHMARKS.items():
        if s.endswith(suffix):
            return bench
    return ("^GSPC", "S&P 500")


def market_close(symbol: str) -> tuple[int, int]:
    s = symbol.upper()
    for suffix, hm in _MARKET_CLOSE.items():
        if s.endswith(suffix):
            return hm
    return (16, 0)


def _num(v):
    try:
        if v is None:
            return None
        v = float(v)
        return None if pd.isna(v) else v
    except Exception:
        return None


def _attr(obj, name):
    try:
        return _num(getattr(obj, name))
    except Exception:
        return None


# --------------------------------------------------------------------------
# Symbol search and profile
# --------------------------------------------------------------------------
def search_symbols(query: str, limit: int = 10) -> list[dict]:
    """Find tickers matching a company name, e.g. 'Tata Motors' -> TATAMOTORS.NS."""
    try:
        res = yf.Search(query, max_results=limit, news_count=0)
        quotes = res.quotes or []
    except Exception:
        return []
    out = []
    for q in quotes:
        sym = q.get("symbol")
        if not sym or q.get("quoteType") not in ("EQUITY", "ETF", "INDEX"):
            continue
        out.append(
            {
                "symbol": sym,
                "name": q.get("longname") or q.get("shortname") or sym,
                "exchange": q.get("exchDisp") or q.get("exchange", ""),
            }
        )
    # Put NSE listings first, BSE next: most useful for Indian users.
    out.sort(key=lambda m: (not m["symbol"].endswith(".NS"), not m["symbol"].endswith(".BO")))
    return out


def get_profile(symbol: str) -> dict:
    prof = {
        "symbol": symbol,
        "name": symbol,
        "currency": "",
        "sector": "",
        "industry": "",
        "exchange": "",
        "market_cap": None,
        "pe": None,
        "summary": "",
        "website": "",
        "officers": [],
    }
    try:
        info = yf.Ticker(symbol).info or {}
        officers = []
        for o in (info.get("companyOfficers") or [])[:12]:
            name = re.sub(r"^(mr|mrs|ms|miss|dr|shri|smt|prof)\.?\s+", "", (o.get("name") or "").strip(), flags=re.I)
            if name:
                officers.append({"name": name, "title": o.get("title") or ""})
        prof["officers"] = officers
        prof["website"] = info.get("website") or ""
        prof.update(
            name=info.get("longName") or info.get("shortName") or symbol,
            currency=info.get("currency") or "",
            sector=info.get("sector") or "",
            industry=info.get("industry") or "",
            exchange=info.get("fullExchangeName") or info.get("exchange") or "",
            market_cap=_num(info.get("marketCap")),
            pe=_num(info.get("trailingPE")),
            summary=info.get("longBusinessSummary") or "",
        )
    except Exception:
        pass
    return prof


# --------------------------------------------------------------------------
# Prices
# --------------------------------------------------------------------------
def get_live_quote(symbol: str) -> dict:
    """Latest price plus today's 1-minute chart. Falls back gracefully."""
    t = yf.Ticker(symbol)
    intraday = pd.DataFrame()
    try:
        intraday = t.history(period="1d", interval="1m")
        if intraday.empty:
            intraday = t.history(period="5d", interval="5m")
            if not intraday.empty:
                last_day = intraday.index[-1].date()
                intraday = intraday[intraday.index.date == last_day]
    except Exception:
        intraday = pd.DataFrame()

    q = {"last": None, "prev_close": None, "open": None, "high": None, "low": None,
         "volume": None, "currency": "", "as_of": None, "market_open": False}
    try:
        fi = t.fast_info
        q["last"] = _attr(fi, "last_price")
        q["prev_close"] = _attr(fi, "regular_market_previous_close") or _attr(fi, "previous_close")
        q["open"] = _attr(fi, "open")
        q["high"] = _attr(fi, "day_high")
        q["low"] = _attr(fi, "day_low")
        q["volume"] = _attr(fi, "last_volume")
        try:
            q["currency"] = fi.currency or ""
        except Exception:
            pass
    except Exception:
        pass

    if not intraday.empty:
        q["last"] = q["last"] or _num(intraday["Close"].iloc[-1])
        q["open"] = q["open"] or _num(intraday["Open"].iloc[0])
        q["high"] = q["high"] or _num(intraday["High"].max())
        q["low"] = q["low"] or _num(intraday["Low"].min())
        q["volume"] = q["volume"] or _num(intraday["Volume"].sum())
        last_bar = intraday.index[-1]
        q["as_of"] = last_bar
        now = datetime.now(timezone.utc)
        q["market_open"] = (now - last_bar.tz_convert("UTC").to_pydatetime()) < timedelta(minutes=10)

    if q["prev_close"] is None:
        try:
            d = t.history(period="5d", interval="1d")
            if len(d) >= 2:
                q["prev_close"] = _num(d["Close"].iloc[-2])
        except Exception:
            pass

    if q["last"] is not None and q["prev_close"]:
        q["change"] = q["last"] - q["prev_close"]
        q["change_pct"] = q["change"] / q["prev_close"]
    else:
        q["change"] = q["change_pct"] = None
    q["intraday"] = intraday
    return q


def get_history(symbol: str, period: str = "6mo") -> pd.DataFrame:
    try:
        df = yf.Ticker(symbol).history(period=period, interval="1d")
        return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()


def get_hourly(symbol: str) -> pd.DataFrame:
    """Hourly bars for the last ~60 days, used for the 'first-hour' reaction."""
    try:
        df = yf.Ticker(symbol).history(period="60d", interval="60m")
        return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()


# --------------------------------------------------------------------------
# News
# --------------------------------------------------------------------------
_SUFFIX_RE = re.compile(
    r"\b(limited|ltd\.?|inc\.?|incorporated|corporation|corp\.?|plc|co\.?|company|holdings?|the)\b",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def clean_company_name(name: str) -> str:
    n = _SUFFIX_RE.sub(" ", name or "")
    n = re.sub(r"[^\w&\s.-]", " ", n)
    return re.sub(r"\s+", " ", n).strip(" .-")


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", text or ""))).strip()


def _yahoo_news(symbol: str) -> list[dict]:
    t = yf.Ticker(symbol)
    try:
        items = t.get_news(count=40) if hasattr(t, "get_news") else t.news
    except Exception:
        try:
            items = t.news
        except Exception:
            items = []
    out = []
    for it in items or []:
        c = it.get("content") if isinstance(it.get("content"), dict) else it
        title = c.get("title")
        if not title:
            continue
        ts = None
        if c.get("pubDate"):
            ts = pd.to_datetime(c["pubDate"], utc=True, errors="coerce")
        elif it.get("providerPublishTime"):
            ts = pd.to_datetime(it["providerPublishTime"], unit="s", utc=True)
        prov = c.get("provider")
        source = prov.get("displayName") if isinstance(prov, dict) else (c.get("publisher") or "Yahoo Finance")
        url = None
        for key in ("canonicalUrl", "clickThroughUrl"):
            if isinstance(c.get(key), dict) and c[key].get("url"):
                url = c[key]["url"]
                break
        url = url or c.get("link")
        out.append(
            {
                "title": title.strip(),
                "source": source or "Yahoo Finance",
                "url": url,
                "published": ts,
                "summary": _strip_html(c.get("summary") or c.get("description") or "")[:500],
                "feed": "Yahoo Finance",
            }
        )
    return out


def _google_news(query: str, days: int, region: str) -> list[dict]:
    if region == "IN":
        loc = "hl=en-IN&gl=IN&ceid=IN:en"
    else:
        loc = "hl=en-US&gl=US&ceid=US:en"
    url = f"https://news.google.com/rss/search?q={quote_plus(query + f' when:{days}d')}&{loc}"
    try:
        resp = requests.get(url, headers=UA, timeout=15)
        feed = feedparser.parse(resp.content)
    except Exception:
        return []
    out = []
    for e in feed.entries:
        title = e.get("title", "")
        source = (e.get("source") or {}).get("title") or ""
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)]
        ts = None
        if e.get("published_parsed"):
            ts = pd.Timestamp(datetime(*e.published_parsed[:6], tzinfo=timezone.utc))
        out.append(
            {
                "title": title.strip(),
                "source": source or "Google News",
                "url": e.get("link"),
                "published": ts,
                "summary": "",
                "feed": "Google News",
            }
        )
    return out


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", t.lower())[:70]


def get_news(symbol: str, query: str, days: int = 30, region: str = "IN") -> list[dict]:
    """Merge Yahoo Finance and Google News headlines, de-duplicated, newest first."""
    items = _yahoo_news(symbol) + _google_news(query, days, region)
    cutoff = pd.Timestamp(datetime.now(timezone.utc) - timedelta(days=days))
    seen, merged = set(), []
    for it in items:
        ts = it["published"]
        if ts is None or pd.isna(ts) or ts < cutoff:
            continue
        key = _norm_title(it["title"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(it)
    merged.sort(key=lambda x: x["published"], reverse=True)
    return merged
