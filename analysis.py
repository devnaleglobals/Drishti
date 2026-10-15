"""Analysis layer: how did the stock react to each headline, and what does it mean?

Method (a simple 'event study', the same idea analysts use):
  * For each headline, find the first trading session that could react to it
    (news after market close counts for the next session).
  * Measure the stock's move from the previous close, and subtract the
    benchmark index move over the same window. What is left is the
    'abnormal' move: the part the overall market does not explain.
  * Compare the move with the headline's tone to label the reaction.
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from data import market_close

DEFAULT_MODEL = "claude-sonnet-5-5"

# --------------------------------------------------------------------------
# Keyword sentiment (used when no AI key is set)
# --------------------------------------------------------------------------
_POS = set(
    """surge surges soar soars jump jumps rally rallies gain gains rise rises beat beats record
    upgrade upgraded upgrades profit profits growth strong stronger outperform bullish win wins
    order orders expansion approval approved dividend bonus acquire acquires acquisition partnership
    raises raised higher boost boosts positive recovery recovers buyback robust tops""".split()
)
_NEG = set(
    """fall falls drop drops plunge plunges slump slumps slide slides decline declines loss losses
    miss misses downgrade downgraded downgrades weak weaker cut cuts probe penalty fine fined lawsuit
    fraud resign resigns resignation bearish selloff lower concern concerns warning warns default
    delay delays ban raid negative crash tumble tumbles layoffs slowdown pressure disappoint""".split()
)


def keyword_sentiment(text: str) -> float:
    words = re.findall(r"[a-z]+", (text or "").lower())
    p = sum(w in _POS for w in words)
    n = sum(w in _NEG for w in words)
    return 0.0 if p + n == 0 else round((p - n) / (p + n), 2)


# --------------------------------------------------------------------------
# Event study
# --------------------------------------------------------------------------
def _by_date(s: pd.Series) -> pd.Series:
    s = s.copy()
    idx = s.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    s.index = idx.normalize()
    return s[~s.index.duplicated(keep="last")]


def _first_hour(ts: pd.Timestamp, hourly: pd.DataFrame | None):
    if hourly is None or hourly.empty:
        return None
    h = hourly["Close"]
    ends = h.index + pd.Timedelta(hours=1)
    before = h[ends <= ts]
    after = h[h.index >= ts]
    if before.empty or after.empty:
        return None
    if after.index[0] - ts > pd.Timedelta(days=4):
        return None
    return float(after.iloc[0] / before.iloc[-1] - 1)


VERDICTS = {
    "Confirmed": "Stock moved the way the news suggested",
    "Contradicted": "Stock moved against the news",
    "Ignored": "No meaningful move beyond the market",
    "Moved on neutral news": "Big move although the headline is neutral",
    "Pending": "Not enough trading sessions since the news",
}


def _verdict(sent, abn, thresh):
    if abn is None or pd.isna(abn):
        return "Pending"
    if abs(abn) < thresh:
        return "Ignored"
    if sent is None or pd.isna(sent) or abs(sent) < 0.15:
        return "Moved on neutral news"
    return "Confirmed" if np.sign(sent) == np.sign(abn) else "Contradicted"


def build_event_table(news: list[dict], daily: pd.DataFrame, bench: pd.DataFrame | None,
                      hourly: pd.DataFrame | None, symbol: str) -> pd.DataFrame:
    if daily is None or daily.empty or not news:
        return pd.DataFrame()

    tz = daily.index.tz
    close = _by_date(daily["Close"])
    vol = _by_date(daily["Volume"])
    bclose = None
    if bench is not None and not bench.empty:
        bclose = _by_date(bench["Close"]).reindex(close.index).ffill()

    rets = close.pct_change()
    abn_daily = rets - bclose.pct_change() if bclose is not None else rets
    sigma = float(abn_daily.std()) if abn_daily.notna().sum() > 5 else 0.015
    thresh = max(0.005, 0.75 * sigma * np.sqrt(2))  # threshold for a 2-session move
    ch, cm = market_close(symbol)
    dates = close.index

    rows = []
    for i, n in enumerate(news):
        ts = n.get("published")
        row = {
            "id": i,
            "published": ts,
            "title": n.get("title"),
            "source": n.get("source"),
            "url": n.get("url"),
            "sentiment": n.get("sentiment"),
            "category": n.get("category", ""),
            "importance": n.get("importance"),
            "why": n.get("why", ""),
            "kind": n.get("kind", "news"),
            "group": n.get("group", "company"),
            "connector": n.get("connector", ""),
            "coverage": n.get("coverage", 1),
            "event_day": pd.NaT,
            "ret_d0": None, "abn_d0": None,
            "ret_d1": None, "abn_d1": None,
            "ret_w": None, "abn_w": None,
            "vol_ratio": None, "first_hour": None, "move_vs_normal": None,
        }
        if ts is not None and not pd.isna(ts):
            t_local = ts.tz_convert(tz) if tz is not None else ts
            news_day = pd.Timestamp(t_local.date())
            after_close = (t_local.hour, t_local.minute) >= (ch, cm)
            idx = int(dates.searchsorted(news_day, side="right" if after_close else "left"))
            row["first_hour"] = _first_hour(ts, hourly)
            if 0 < idx < len(dates):
                base = close.iloc[idx - 1]
                row["event_day"] = dates[idx]

                def cum(k):
                    j = idx + k
                    if j >= len(dates):
                        return None, None
                    r = float(close.iloc[j] / base - 1)
                    if bclose is None or pd.isna(bclose.iloc[j]) or pd.isna(bclose.iloc[idx - 1]):
                        return r, r
                    return r, r - float(bclose.iloc[j] / bclose.iloc[idx - 1] - 1)

                row["ret_d0"], row["abn_d0"] = cum(0)
                row["ret_d1"], row["abn_d1"] = cum(1)
                row["ret_w"], row["abn_w"] = cum(4)
                prior = vol.iloc[max(0, idx - 20):idx].mean()
                if prior and prior > 0:
                    row["vol_ratio"] = float(vol.iloc[idx] / prior)
        rows.append(row)

    df = pd.DataFrame(rows)
    key = df["abn_d1"].where(df["abn_d1"].notna(), df["abn_d0"])
    df["key_move"] = key
    df["move_vs_normal"] = (key.abs() / (sigma * np.sqrt(2))).where(key.notna())
    df["verdict"] = [_verdict(s, a, thresh) for s, a in zip(df["sentiment"], key)]
    df["same_day_news"] = df.groupby("event_day")["id"].transform("count").fillna(1).astype(int)
    df.attrs["sigma"] = sigma
    df.attrs["threshold"] = thresh
    return df


# --------------------------------------------------------------------------
# Price summary
# --------------------------------------------------------------------------
def price_summary(daily: pd.DataFrame, bench: pd.DataFrame | None, bench_name: str, quote: dict) -> dict:
    out = {}
    if daily is None or daily.empty:
        return out
    c = daily["Close"]

    def chg(n):
        return float(c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else None

    out["last_close"] = float(c.iloc[-1])
    out["live_price"] = quote.get("last")
    out["chg_1w"] = chg(5)
    out["chg_1m"] = chg(21)
    out["chg_period"] = float(c.iloc[-1] / c.iloc[0] - 1)
    out["period_high"] = float(daily["High"].max())
    out["period_low"] = float(daily["Low"].min())
    out["volatility_annual"] = float(c.pct_change().std() * np.sqrt(252))
    out["start"] = daily.index[0].date().isoformat()
    out["end"] = daily.index[-1].date().isoformat()
    if bench is not None and not bench.empty:
        b = bench["Close"]
        out["bench_name"] = bench_name
        out["bench_chg_period"] = float(b.iloc[-1] / b.iloc[0] - 1)
    return out


def summary_text(company: str, symbol: str, s: dict) -> str:
    def p(v):
        return "n/a" if v is None else f"{v * 100:+.2f}%"

    lines = [
        f"Company: {company} ({symbol})",
        f"Period: {s.get('start')} to {s.get('end')}",
        f"Live/last price: {s.get('live_price') or s.get('last_close'):.2f}",
        f"Change: 1 week {p(s.get('chg_1w'))}, 1 month {p(s.get('chg_1m'))}, whole period {p(s.get('chg_period'))}",
        f"Period high {s.get('period_high'):.2f}, low {s.get('period_low'):.2f}, "
        f"annualised volatility {s.get('volatility_annual', 0) * 100:.1f}%",
    ]
    if s.get("bench_name"):
        lines.append(f"{s['bench_name']} over same period: {p(s.get('bench_chg_period'))}")
    return "\n".join(lines)


def events_text(df: pd.DataFrame, limit: int = 40) -> str:
    if df.empty:
        return "No headlines."
    d = df.copy()
    d["_rank"] = d["key_move"].abs().fillna(0)
    d = d.sort_values("_rank", ascending=False).head(limit).sort_values("published")

    def p(v):
        return "n/a" if v is None or pd.isna(v) else f"{v * 100:+.2f}%"

    out = []
    for _, r in d.iterrows():
        when = r["published"].strftime("%Y-%m-%d %H:%M UTC") if pd.notna(r["published"]) else "?"
        sent = "n/a" if pd.isna(r["sentiment"]) else f"{r['sentiment']:+.2f}"
        vr = "n/a" if pd.isna(r["vol_ratio"]) else f"{r['vol_ratio']:.1f}x"
        out.append(
            f"- {when} | {r['source']} | \"{r['title']}\" | tone {sent} | "
            f"first hour {p(r['first_hour'])} | reaction day abn {p(r['abn_d0'])} | "
            f"2-day abn {p(r['abn_d1'])} | 1-week abn {p(r['abn_w'])} | volume {vr} | "
            f"headlines same day {r['same_day_news']} | verdict {r['verdict']}"
        )
    return "\n".join(out)


def rule_based_summary(df: pd.DataFrame, s: dict) -> list[str]:
    """Plain-language findings that need no AI."""
    notes = []
    if s:
        stock, bench = s.get("chg_period"), s.get("bench_chg_period")
        if stock is not None and bench is not None:
            diff = stock - bench
            word = "beat" if diff > 0 else "lagged"
            notes.append(
                f"Over the period the stock moved {stock * 100:+.1f}% and {word} "
                f"{s['bench_name']} ({bench * 100:+.1f}%) by {abs(diff) * 100:.1f} points."
            )
    if df.empty:
        return notes
    done = df[df["verdict"] != "Pending"]
    if len(done):
        share = done["verdict"].value_counts(normalize=True)
        notes.append(
            f"Of {len(done)} headlines with a measurable reaction, the market confirmed "
            f"{share.get('Confirmed', 0) * 100:.0f}%, went the other way on "
            f"{share.get('Contradicted', 0) * 100:.0f}% and ignored {share.get('Ignored', 0) * 100:.0f}%."
        )
    movers = df.dropna(subset=["key_move"])
    if len(movers):
        up = movers.loc[movers["key_move"].idxmax()]
        dn = movers.loc[movers["key_move"].idxmin()]
        if up["key_move"] > 0:
            notes.append(f"Strongest positive reaction ({up['key_move'] * 100:+.1f}% vs market): \"{up['title']}\".")
        if dn["key_move"] < 0:
            notes.append(f"Strongest negative reaction ({dn['key_move'] * 100:+.1f}% vs market): \"{dn['title']}\".")
    crowded = df[(df["same_day_news"] > 3) & df["key_move"].notna()]
    if len(crowded):
        notes.append(
            "Some days had several headlines at once, so the move on those days cannot be pinned on one story."
        )
    return notes


# --------------------------------------------------------------------------
# AI: works with Azure OpenAI or Anthropic Claude
# --------------------------------------------------------------------------
# cfg is a dict:
#   Azure:     {"provider": "azure", "api_key", "endpoint", "deployment", "api_version"}
#   Anthropic: {"provider": "anthropic", "api_key", "model"}


def llm_ready(cfg: dict) -> bool:
    if not cfg or not cfg.get("api_key"):
        return False
    if cfg.get("provider") == "azure":
        return all(cfg.get(k) for k in ("endpoint", "deployment", "api_version"))
    return bool(cfg.get("model"))


def _complete(cfg: dict, system: str, messages: list[dict], max_tokens: int) -> str:
    """Send one chat request to whichever provider is configured and return the text."""
    if cfg.get("provider") == "azure":
        from openai import AzureOpenAI, BadRequestError

        client = AzureOpenAI(
            api_key=cfg["api_key"],
            azure_endpoint=cfg["endpoint"],
            api_version=cfg["api_version"],
        )
        msgs = [{"role": "system", "content": system}] + messages
        try:
            resp = client.chat.completions.create(
                model=cfg["deployment"], messages=msgs, max_tokens=max_tokens)
        except BadRequestError as e:
            # Newer models (o-series, GPT-5 family) want max_completion_tokens instead.
            if "max_completion_tokens" not in str(e):
                raise
            resp = client.chat.completions.create(
                model=cfg["deployment"], messages=msgs, max_completion_tokens=max_tokens * 4)
        return resp.choices[0].message.content or ""

    import anthropic

    client = anthropic.Anthropic(api_key=cfg["api_key"])
    msg = client.messages.create(model=cfg["model"], max_tokens=max_tokens, system=system, messages=messages)
    return "".join(getattr(b, "text", "") for b in msg.content)


def _json_array(text: str):
    text = re.sub(r"```(?:json)?", "", text)
    a, b = text.find("["), text.rfind("]")
    if a == -1 or b == -1:
        raise ValueError("AI response did not contain a JSON list")
    return json.loads(text[a: b + 1])


def ai_score_news(cfg: dict, company: str, symbol: str, items: list[tuple]) -> dict:
    """items: (id, time, source, title, summary). Returns {id: {...}}."""
    results = {}
    system = (
        "You are a sell-side equity analyst. You judge news headlines strictly from the point of view "
        "of a shareholder of the named company. Reply with a JSON array only, no prose."
    )
    for start in range(0, len(items), 40):
        batch = items[start: start + 40]
        listing = "\n".join(f"[{i}] {t} | {src} | {title} | {summ[:240]}" for i, t, src, title, summ in batch)
        prompt = f"""Company: {company} (ticker {symbol})

Score each headline below. Return a JSON array, one object per headline:
{{"id": <number>, "relevant": true/false (is it really about this company or its business?),
  "sentiment": number from -1 (very bad for the stock) to 1 (very good),
  "category": one of ["Earnings", "Guidance", "Deal/M&A", "Orders/Contracts", "Management",
                      "Regulatory/Legal", "Product", "Analyst rating", "Macro/Sector", "Market chatter", "Other"],
  "importance": 1-5 (how much it could move the price),
  "why": "max 18 words on why it matters"}}

Headlines:
{listing}"""
        text = _complete(cfg, system, [{"role": "user", "content": prompt}], max_tokens=4000)
        for obj in _json_array(text):
            try:
                results[int(obj["id"])] = obj
            except Exception:
                continue
    return results


def ai_postmortem(cfg: dict, company: str, symbol: str, summary: str, events: str) -> str:
    system = ("You are an equity analyst who explains stock moves in plain English. You never give buy/sell advice. "
              "Never mention which AI company or model wrote the report.")
    prompt = f"""You are writing a news-vs-price post-mortem for {company} ({symbol}).

PRICE SUMMARY
{summary}

HEADLINES WITH MEASURED REACTIONS
(abn = move after subtracting the benchmark index; 2-day abn is the main reaction measure;
volume = reaction-day volume vs 20-day average)
{events}

Write the report in Markdown with these sections:
## One-line verdict
## What actually moved the stock
(the 3-5 headlines with real impact, with numbers)
## News the market ignored
## Where the market disagreed with the headline
(and plausible reasons: already priced in, sell-the-news, bigger macro move, etc.)
## The story the price is telling now
## What to watch next
## Caveats
(several headlines on one day, correlation is not causation, data delays)

Be specific, use the numbers, keep it under 600 words, write in plain English a non-expert can follow.
Do not give buy/sell advice."""
    return _complete(cfg, system, [{"role": "user", "content": prompt}], max_tokens=2500)


def ai_chat(cfg: dict, context: str, history: list[dict], question: str) -> str:
    system = (
        "You are a careful equity analyst helping a user understand how a stock reacted to news. "
        "Answer from the data provided; say clearly when the data cannot answer something. "
        "Keep answers short and plain. Do not give buy/sell recommendations. "
        f"If asked who or what you are, say you are {cfg.get('display_name', 'the AI Analyst')}, the analysis "
        "assistant in this app. Never name the company, model or technology behind you.\n\nDATA\n" + context
    )
    msgs = [{"role": m["role"], "content": m["content"]} for m in history[-10:]]
    msgs.append({"role": "user", "content": question})
    return _complete(cfg, system, msgs, max_tokens=1200)


def ai_suggest_keywords(cfg: dict, info: dict, profile: dict, commodity_list: list[dict]) -> dict:
    """Ask the AI for a fuller keyword profile. Returns a dict in the profile shape (not saved)."""
    officers = ", ".join(f"{o['name']} ({o['title']})" for o in info.get("officers", [])[:8]) or "unknown"
    tickers = "\n".join(f"{c['ticker']}: {c['name']}" for c in commodity_list)
    prompt = f"""Build a news-monitoring keyword profile for this listed company.

Company: {info.get('name')} (ticker {profile.get('symbol')})
Sector: {info.get('sector')}, industry: {info.get('industry')}
Listed officers: {officers}
Business summary: {(info.get('summary') or '')[:1500]}
Current profile: {json.dumps({k: profile.get(k) for k in ('company', 'people', 'products', 'industry', 'materials', 'exclude', 'commodities')})}

Return ONE JSON object with these keys (arrays of short strings, max 8 each):
"company": official name, common short names and abbreviations used in Indian/global media,
"people": current CEO, chairman, MD, founders, CFO (full names, only people you are confident hold the role),
"products": major brands, subsidiaries, business units,
"industry": 3-6 main listed competitors plus 1-2 sector phrases,
"materials": raw materials, inputs or commodities whose price moves affect this company, as news search phrases (e.g. "crude oil price"),
"exclude": words that would indicate an unrelated namesake (often empty),
"commodities": tickers from this list ONLY that matter for this company (max 4):
{tickers}
JSON only, no prose."""
    text = _complete(cfg, "You are an equity research analyst. Reply with JSON only.",
                     [{"role": "user", "content": prompt}], max_tokens=1500)
    text = re.sub(r"```(?:json)?", "", text)
    a, b = text.find("{"), text.rfind("}")
    if a == -1 or b == -1:
        raise ValueError("AI did not return a keyword profile")
    return json.loads(text[a: b + 1])
