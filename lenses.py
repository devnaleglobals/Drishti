"""Lens calculations. Each lens returns chart series, tables and an overview 'tile'.

Tile status values: 'positive', 'negative', 'neutral', 'caution'.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analysis import _by_date


def _d(idx) -> list[str]:
    return [t.strftime("%Y-%m-%d") for t in idx]


def _series(s: pd.Series) -> list[dict]:
    return [{"time": t, "value": float(v)} for t, v in zip(_d(s.index), s.values) if pd.notna(v)]


def _pct(v):
    return "n/a" if v is None or pd.isna(v) else f"{v * 100:+.1f}%"


def _tile(lens, title, status, metric, label, reading):
    return {"lens": lens, "title": title, "status": status, "metric": metric, "label": label, "reading": reading}


# --------------------------------------------------------------------------
# Technicals
# --------------------------------------------------------------------------
def technical_lens(daily: pd.DataFrame) -> dict:
    c = daily["Close"]
    sma20, sma50 = c.rolling(20).mean(), c.rolling(50).mean()
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    sig = macd.ewm(span=9, adjust=False).mean()
    hist = macd - sig

    last = float(c.iloc[-1])
    signals, score = [], 0
    s20, s50 = sma20.iloc[-1], sma50.iloc[-1]
    if pd.notna(s50):
        if last > s20 and last > s50:
            signals.append({"name": "Trend", "status": "positive", "value": "Uptrend",
                            "reading": "Price is above its 20-day and 50-day averages."})
            score += 1
        elif last < s20 and last < s50:
            signals.append({"name": "Trend", "status": "negative", "value": "Downtrend",
                            "reading": "Price is below its 20-day and 50-day averages."})
            score -= 1
        else:
            signals.append({"name": "Trend", "status": "neutral", "value": "Mixed",
                            "reading": "Price sits between its 20-day and 50-day averages."})
    elif pd.notna(s20):
        st_ = "positive" if last > s20 else "negative"
        signals.append({"name": "Trend", "status": st_, "value": "Above 20-day" if last > s20 else "Below 20-day",
                        "reading": "Not enough history for the 50-day average. Pick a longer period."})

    r = rsi.iloc[-1]
    if pd.notna(r):
        if r >= 70:
            signals.append({"name": "RSI (14)", "status": "caution", "value": f"{r:.0f}",
                            "reading": "Overbought zone. Rallies this stretched often pause."})
        elif r <= 30:
            signals.append({"name": "RSI (14)", "status": "caution", "value": f"{r:.0f}",
                            "reading": "Oversold zone. Selling may be exhausted, or the fall may continue."})
        else:
            signals.append({"name": "RSI (14)", "status": "neutral", "value": f"{r:.0f}",
                            "reading": "No extreme. Momentum is within the normal range."})

    h = hist.dropna()
    if len(h) > 5:
        crossed = np.sign(h.iloc[-1]) != np.sign(h.iloc[-6])
        pos = h.iloc[-1] > 0
        signals.append({
            "name": "MACD", "status": "positive" if pos else "negative",
            "value": ("Bullish" if pos else "Bearish") + (" crossover" if crossed else ""),
            "reading": ("Momentum turned up in the last week." if pos else "Momentum turned down in the last week.")
            if crossed else ("Short-term momentum is above its signal line." if pos
                             else "Short-term momentum is below its signal line."),
        })
        score += 1 if pos else -1

    lo, hi = float(daily["Low"].min()), float(daily["High"].max())
    pos_in_range = (last - lo) / (hi - lo) if hi > lo else 0.5
    signals.append({"name": "Range position", "status": "neutral", "value": f"{pos_in_range * 100:.0f}%",
                    "reading": f"0% is the period low ({lo:,.2f}), 100% is the period high ({hi:,.2f})."})

    status = "positive" if score >= 2 else "negative" if score <= -2 else "neutral"
    trend_value = signals[0]["value"] if signals else "n/a"
    tile = _tile("technical", "Technicals", status, trend_value, "Trend",
                 f"RSI {r:.0f}, price at {pos_in_range * 100:.0f}% of the period range." if pd.notna(r)
                 else "Pick a longer period for full indicators.")
    return {"sma20": _series(sma20), "sma50": _series(sma50), "rsi": _series(rsi), "macd": _series(macd),
            "signal": _series(sig), "hist": _series(hist), "signals": signals, "tile": tile}


# --------------------------------------------------------------------------
# Risk
# --------------------------------------------------------------------------
def risk_lens(daily: pd.DataFrame, bench: pd.DataFrame | None) -> dict:
    c = _by_date(daily["Close"])
    r = c.pct_change().dropna()
    vol = float(r.std() * np.sqrt(252)) if len(r) > 2 else None
    dd = c / c.cummax() - 1
    out = {
        "volatility": vol,
        "max_drawdown": float(dd.min()),
        "max_drawdown_date": dd.idxmin().strftime("%Y-%m-%d"),
        "current_drawdown": float(dd.iloc[-1]),
        "var95": float(-np.percentile(r, 5)) if len(r) > 10 else None,
        "best_day": float(r.max()) if len(r) else None,
        "best_day_date": r.idxmax().strftime("%Y-%m-%d") if len(r) else None,
        "worst_day": float(r.min()) if len(r) else None,
        "worst_day_date": r.idxmin().strftime("%Y-%m-%d") if len(r) else None,
        "up_days": float((r > 0).mean()) if len(r) else None,
        "beta": None, "correlation": None,
        "drawdown": _series(dd),
    }
    if bench is not None and not bench.empty:
        b = _by_date(bench["Close"]).reindex(c.index).ffill().pct_change()
        j = pd.concat([r, b], axis=1, join="inner").dropna()
        if len(j) > 10 and j.iloc[:, 1].var() > 0:
            out["beta"] = float(j.iloc[:, 0].cov(j.iloc[:, 1]) / j.iloc[:, 1].var())
            out["correlation"] = float(j.iloc[:, 0].corr(j.iloc[:, 1]))
    if len(r) > 10:
        counts, edges = np.histogram(r * 100, bins=24)
        out["distribution"] = [{"from": float(edges[i]), "to": float(edges[i + 1]), "count": int(counts[i])}
                               for i in range(len(counts))]
    else:
        out["distribution"] = []

    cur = out["current_drawdown"]
    status = "negative" if cur <= -0.15 else "caution" if cur <= -0.07 or (vol or 0) > 0.40 else "neutral"
    beta_txt = f", beta {out['beta']:.2f}" if out["beta"] is not None else ""
    where = "Trading at or near its high" if cur > -0.01 else f"{_pct(cur)} below its high"
    out["tile"] = _tile("risk", "Risk", status, f"{(vol or 0) * 100:.0f}%", "Annual volatility",
                        f"{where}, worst drop {_pct(out['max_drawdown'])}{beta_txt}.")
    return out


# --------------------------------------------------------------------------
# Relative strength
# --------------------------------------------------------------------------
def relative_lens(daily: pd.DataFrame, bench: pd.DataFrame | None, bench_name: str) -> dict:
    s = _by_date(daily["Close"])
    out = {"bench_name": bench_name, "stock": _series(s / s.iloc[0] * 100), "bench": [], "ratio": [], "windows": []}
    if bench is None or bench.empty:
        out["tile"] = _tile("relative", "Relative strength", "neutral", "n/a", f"vs {bench_name}",
                            "Benchmark data not available.")
        return out
    b = _by_date(bench["Close"]).reindex(s.index).ffill().bfill()
    out["bench"] = _series(b / b.iloc[0] * 100)
    ratio = (s / b) / (s.iloc[0] / b.iloc[0]) * 100
    out["ratio"] = _series(ratio)
    for label, n in (("1 week", 5), ("1 month", 21), ("3 months", 63), ("Whole period", len(s) - 1)):
        if len(s) > n >= 1:
            sr, br = float(s.iloc[-1] / s.iloc[-1 - n] - 1), float(b.iloc[-1] / b.iloc[-1 - n] - 1)
            out["windows"].append({"window": label, "stock": sr, "bench": br, "diff": sr - br})
    whole = out["windows"][-1] if out["windows"] else None
    month = next((w for w in out["windows"] if w["window"] == "1 month"), None)
    if whole:
        status = "positive" if whole["diff"] > 0.02 else "negative" if whole["diff"] < -0.02 else "neutral"
        verb = "Beating" if whole["diff"] > 0 else "Lagging"
        reading = f"{verb} {bench_name} by {abs(whole['diff']) * 100:.1f} points over the period"
        if month:
            reading += f", {_pct(month['diff'])} over the last month."
        out["tile"] = _tile("relative", "Relative strength", status, _pct(whole["diff"]), f"vs {bench_name}", reading)
    else:
        out["tile"] = _tile("relative", "Relative strength", "neutral", "n/a", f"vs {bench_name}", "Not enough data.")
    return out


# --------------------------------------------------------------------------
# Sentiment
# --------------------------------------------------------------------------
def sentiment_lens(events: pd.DataFrame) -> dict:
    out = {"daily": [], "categories": [], "sources": [], "avg": None, "positive": 0, "negative": 0, "neutral": 0,
           "recent_avg": None, "prior_avg": None}
    if events.empty:
        out["tile"] = _tile("sentiment", "Sentiment", "neutral", "n/a", "Average tone", "No headlines in this window.")
        return out
    e = events.copy()
    e["sentiment"] = pd.to_numeric(e["sentiment"], errors="coerce")
    s = e["sentiment"].dropna()
    out["avg"] = float(s.mean()) if len(s) else None
    out["positive"] = int((s > 0.15).sum())
    out["negative"] = int((s < -0.15).sum())
    out["neutral"] = int(len(s) - out["positive"] - out["negative"])

    ed = e.dropna(subset=["event_day"])
    if not ed.empty:
        g = ed.groupby("event_day").agg(avg=("sentiment", "mean"), n=("id", "count"))
        out["daily"] = [{"time": d.strftime("%Y-%m-%d"), "value": float(r["avg"]) if pd.notna(r["avg"]) else 0.0,
                         "count": int(r["n"])} for d, r in g.iterrows()]

    pub = e.dropna(subset=["published"]).sort_values("published")
    if len(pub):
        cut = pub["published"].max() - pd.Timedelta(days=7)
        rec, pri = pub[pub["published"] >= cut]["sentiment"], pub[pub["published"] < cut]["sentiment"]
        out["recent_avg"] = float(rec.mean()) if rec.notna().any() else None
        out["prior_avg"] = float(pri.mean()) if pri.notna().any() else None

    e["category"] = e["category"].replace("", np.nan).fillna("Uncategorised")
    e["absmove"] = pd.to_numeric(e["key_move"], errors="coerce").abs()
    for cat, g in e.groupby("category"):
        out["categories"].append({"category": cat, "count": int(len(g)),
                                  "avg_sentiment": float(g["sentiment"].mean()) if g["sentiment"].notna().any() else None,
                                  "avg_move": float(g["absmove"].mean()) if g["absmove"].notna().any() else None})
    out["categories"].sort(key=lambda x: -x["count"])
    for src, g in e.groupby("source"):
        out["sources"].append({"source": src, "count": int(len(g)),
                               "avg_sentiment": float(g["sentiment"].mean()) if g["sentiment"].notna().any() else None})
    out["sources"] = sorted(out["sources"], key=lambda x: -x["count"])[:10]

    avg = out["avg"] or 0
    status = "positive" if avg > 0.15 else "negative" if avg < -0.15 else "neutral"
    total = max(len(s), 1)
    reading = f"{out['positive'] / total * 100:.0f}% positive, {out['negative'] / total * 100:.0f}% negative headlines."
    if out["recent_avg"] is not None and out["prior_avg"] is not None:
        shift = out["recent_avg"] - out["prior_avg"]
        if abs(shift) > 0.15:
            reading += " Tone has " + ("improved" if shift > 0 else "worsened") + " in the last 7 days."
    out["tile"] = _tile("sentiment", "Sentiment", status, f"{avg:+.2f}", "Average tone (-1 to +1)", reading)
    return out


# --------------------------------------------------------------------------
# Volume and attention
# --------------------------------------------------------------------------
def attention_lens(daily: pd.DataFrame, events: pd.DataFrame) -> dict:
    vol = _by_date(daily["Volume"])
    close = _by_date(daily["Close"])
    avg20 = vol.rolling(20, min_periods=5).mean().shift(1)
    ratio = vol / avg20
    news_count = {}
    top_title = {}
    if not events.empty:
        ed = events.dropna(subset=["event_day"])
        for d, g in ed.groupby("event_day"):
            news_count[d] = len(g)
            ranked = g.assign(_imp=pd.to_numeric(g["importance"], errors="coerce").fillna(0)).sort_values(
                "_imp", ascending=False)
            top_title[d] = ranked["title"].iloc[0]
    rets = close.pct_change()
    volume_series = [{"time": d.strftime("%Y-%m-%d"), "value": float(v), "news": int(news_count.get(d, 0))}
                     for d, v in vol.items() if pd.notna(v)]
    spikes = []
    for d, rv in ratio.items():
        if pd.notna(rv) and rv >= 2:
            spikes.append({"date": d.strftime("%Y-%m-%d"), "ratio": float(rv),
                           "return": float(rets.get(d)) if pd.notna(rets.get(d)) else None,
                           "news": int(news_count.get(d, 0)), "headline": top_title.get(d, "")})
    spikes.sort(key=lambda x: x["date"], reverse=True)
    quiet = sum(1 for s in spikes if s["news"] == 0)
    last_ratio = float(ratio.iloc[-1]) if pd.notna(ratio.iloc[-1]) else None
    status = "caution" if quiet else ("positive" if spikes else "neutral")
    reading = (f"{len(spikes)} volume spikes (2x normal or more)"
               + (f", {quiet} with no matching headline." if quiet else ", all with news behind them.")
               if spikes else "No unusual volume in this period.")
    tile = _tile("attention", "Volume and attention", status,
                 f"{last_ratio:.1f}x" if last_ratio else "n/a", "Latest volume vs usual", reading)
    return {"volume": volume_series, "avg20": _series(avg20), "spikes": spikes, "quiet_spikes": quiet, "tile": tile}


# --------------------------------------------------------------------------
# News impact tile
# --------------------------------------------------------------------------
def impact_tile(events: pd.DataFrame) -> dict:
    if events.empty:
        return _tile("impact", "News impact", "neutral", "0", "Headlines", "No headlines found in this window.")
    done = events[events["verdict"] != "Pending"]
    vc = done["verdict"].value_counts()
    n = max(len(done), 1)
    conf, contra = vc.get("Confirmed", 0), vc.get("Contradicted", 0)
    ignored = vc.get("Ignored", 0)
    if ignored / n > 0.5:
        status = "neutral"
    else:
        status = "positive" if conf > contra else "caution" if contra > conf else "neutral"
    movers = events.dropna(subset=["key_move"])
    biggest = ""
    if len(movers):
        r = movers.loc[movers["key_move"].abs().idxmax()]
        biggest = f" Biggest reaction {_pct(r['key_move'])} vs market."
    return _tile("impact", "News impact", status, f"{conf / n * 100:.0f}%", "Headlines the market confirmed",
                 f"{len(events)} headlines, {contra} contradicted, {vc.get('Ignored', 0)} ignored.{biggest}")


# --------------------------------------------------------------------------
# Materials: how the stock moves with its key commodities
# --------------------------------------------------------------------------
def materials_lens(daily: pd.DataFrame, commodities: dict, events: pd.DataFrame) -> dict:
    """commodities: {ticker: (name, DataFrame)}"""
    s = _by_date(daily["Close"])
    sr = s.pct_change()
    rows, series = [], {"stock": _series(s / s.iloc[0] * 100)}
    for tk, (name, df) in commodities.items():
        if df is None or df.empty:
            rows.append({"ticker": tk, "name": name, "available": False})
            continue
        c = _by_date(df["Close"]).reindex(s.index).ffill()
        if c.notna().sum() < 10:
            rows.append({"ticker": tk, "name": name, "available": False})
            continue
        cr = c.pct_change()
        j = pd.concat([sr, cr, cr.shift(1)], axis=1).dropna()
        corr = float(j.iloc[:, 0].corr(j.iloc[:, 1])) if len(j) > 15 else None
        lead = float(j.iloc[:, 0].corr(j.iloc[:, 2])) if len(j) > 15 else None
        beta = float(j.iloc[:, 0].cov(j.iloc[:, 1]) / j.iloc[:, 1].var()) if len(j) > 15 and j.iloc[:, 1].var() > 0 else None
        cv = c.dropna()

        def chg(n):
            return float(cv.iloc[-1] / cv.iloc[-1 - n] - 1) if len(cv) > n else None

        first = cv.iloc[0]
        series[tk] = _series(c / first * 100)
        rows.append({"ticker": tk, "name": name, "available": True, "last": float(cv.iloc[-1]),
                     "chg_1w": chg(5), "chg_1m": chg(21), "chg_period": float(cv.iloc[-1] / first - 1),
                     "corr": corr, "lead_corr": lead, "beta": beta})

    avail = [r for r in rows if r.get("available") and r.get("corr") is not None]
    mat_news = []
    if not events.empty and "group" in events:
        m = events[events["group"] == "materials"].sort_values("published", ascending=False).head(15)
        mat_news = [{"title": r["title"], "source": r["source"], "url": r["url"],
                     "published": r["published"].isoformat() if pd.notna(r["published"]) else None,
                     "sentiment": None if pd.isna(r["sentiment"]) else float(r["sentiment"])} for _, r in m.iterrows()]
    if avail:
        top = max(avail, key=lambda r: abs(r["corr"]))
        strength = "strong" if abs(top["corr"]) >= 0.5 else "moderate" if abs(top["corr"]) >= 0.25 else "weak"
        direction = "together with" if top["corr"] > 0 else "against"
        reading = f"Moves {direction} {top['name']} ({strength}, correlation {top['corr']:+.2f})."
        big = [r for r in avail if r.get("chg_1m") is not None and abs(r["chg_1m"]) >= 0.05]
        if big:
            b = max(big, key=lambda r: abs(r["chg_1m"]))
            reading += f" {b['name']} {_pct(b['chg_1m'])} in a month."
        status = "caution" if big and abs(top["corr"]) >= 0.25 else "neutral"
        tile = _tile("materials", "Materials", status, f"{top['corr']:+.2f}", f"Link to {top['name']}", reading)
    else:
        tile = _tile("materials", "Materials", "neutral", "n/a", "Commodity link",
                     "Pick commodities for this company on the Keywords and sources lens.")
    return {"rows": rows, "series": series, "news": mat_news, "tile": tile}


# --------------------------------------------------------------------------
# News feed freshness
# --------------------------------------------------------------------------
def feed_tile(items: list[dict], health: list[dict]) -> dict:
    now = pd.Timestamp.now(tz="UTC")
    hour = sum(1 for i in items if now - i["published"] <= pd.Timedelta(hours=1))
    day = sum(1 for i in items if now - i["published"] <= pd.Timedelta(hours=24))
    active = sum(1 for h in health if h["status"] == "ok")
    tried = sum(1 for h in health if h["status"] != "off")
    newest = items[0]["published"] if items else None
    if newest is not None:
        mins = int((now - newest).total_seconds() // 60)
        age = f"{mins} min ago" if mins < 60 else f"{mins // 60} h ago" if mins < 1440 else f"{mins // 1440} d ago"
    else:
        age = "none"
    status = "positive" if hour else "neutral"
    return _tile("feed", "News feed", status, str(day), "Items in the last 24 hours",
                 f"{hour} in the last hour, newest {age}. {active} of {tried} sources returned results.")
