"""Keyword profile for each company: what to search for across all sources.

Groups:
  company    names and short names of the company
  people     CEO, chairman, founders, other leaders
  products   brands, subsidiaries, business units
  industry   competitors, sector terms
  materials  raw materials and commodities that drive costs or revenue
  exclude    words that mark a result as unrelated (e.g. a namesake)
Plus 'commodities' (price tickers for the Materials lens) and 'extra_feeds' (RSS URLs).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE / "data")) / "keywords"

GROUPS = ["company", "people", "products", "industry", "materials"]
GROUP_LABELS = {
    "company": "Company",
    "people": "Leadership",
    "products": "Products and units",
    "industry": "Industry and peers",
    "materials": "Materials",
}

# Commodity and macro price series available free on Yahoo Finance.
COMMODITIES = {
    "BZ=F": ("Brent crude oil", ["Brent", "crude oil price"]),
    "CL=F": ("WTI crude oil", ["crude oil", "WTI"]),
    "NG=F": ("Natural gas", ["natural gas price", "LNG"]),
    "HO=F": ("Diesel / heating oil", ["diesel price"]),
    "GC=F": ("Gold", ["gold price"]),
    "SI=F": ("Silver", ["silver price"]),
    "PL=F": ("Platinum", ["platinum price"]),
    "PA=F": ("Palladium", ["palladium price"]),
    "HG=F": ("Copper", ["copper price"]),
    "ALI=F": ("Aluminium", ["aluminium price", "aluminum price"]),
    "HRC=F": ("Steel (hot-rolled coil)", ["steel price", "hot-rolled coil"]),
    "ZW=F": ("Wheat", ["wheat price"]),
    "ZC=F": ("Corn", ["corn price"]),
    "ZS=F": ("Soybeans", ["soybean price"]),
    "ZL=F": ("Soybean oil (edible oil proxy)", ["edible oil price", "palm oil"]),
    "SB=F": ("Sugar", ["sugar price"]),
    "CT=F": ("Cotton", ["cotton price"]),
    "KC=F": ("Coffee", ["coffee price"]),
    "CC=F": ("Cocoa", ["cocoa price"]),
    "INR=X": ("USD/INR exchange rate", ["rupee", "USD/INR"]),
    "DX-Y.NYB": ("US dollar index", ["dollar index"]),
}

# Industry words -> commodities that usually matter.
_SECTOR_MAP = [
    (("oil", "gas", "refin", "petro", "energy"), ["BZ=F", "CL=F", "NG=F"]),
    (("steel", "iron"), ["HRC=F"]),
    (("aluminum", "aluminium"), ["ALI=F"]),
    (("copper", "metal", "mining"), ["HG=F", "ALI=F"]),
    (("gold", "jewel", "luxury"), ["GC=F", "SI=F"]),
    (("auto", "vehicle", "tyre", "tire"), ["HRC=F", "ALI=F", "HG=F"]),
    (("paint", "chemical", "specialty"), ["BZ=F"]),
    (("airline", "airport", "aviation"), ["BZ=F"]),
    (("software", "information technology", "it services", "consulting"), ["INR=X"]),
    (("pharma", "drug", "biotech"), ["INR=X"]),
    (("sugar",), ["SB=F"]),
    (("textile", "apparel"), ["CT=F"]),
    (("food", "packaged", "household", "personal products", "tobacco", "beverage"), ["ZL=F", "ZW=F", "SB=F"]),
    (("cement", "building"), ["BZ=F"]),
    (("utilities", "power", "electric"), ["NG=F"]),
]

_SUFFIX_RE = re.compile(
    r"\b(limited|ltd\.?|inc\.?|incorporated|corporation|corp\.?|plc|co\.?|company|holdings?|the)\b", re.I)
_LEADER_RE = re.compile(r"\b(ceo|chief executive|chairman|chairperson|managing director|founder|president|cfo|chief financial)\b", re.I)


def _clean_name(name: str) -> str:
    n = _SUFFIX_RE.sub(" ", name or "")
    return re.sub(r"\s+", " ", n).strip(" .,-&")


def _uniq(items):
    seen, out = set(), []
    for x in items:
        x = (x or "").strip()
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def default_profile(symbol: str, info: dict) -> dict:
    """Build a starting profile from public company data (no AI needed)."""
    base = symbol.split(".")[0].upper()
    long_name = info.get("name") or symbol
    company = _uniq([_clean_name(long_name), long_name if long_name != symbol else "", base if len(base) >= 3 else ""])

    leaders = [o for o in info.get("officers", []) if _LEADER_RE.search(o.get("title", ""))]
    people = _uniq([o["name"] for o in leaders])[:4]

    text = f"{info.get('sector', '')} {info.get('industry', '')}".lower()
    tickers = []
    for words, tks in _SECTOR_MAP:
        if any(w in text for w in words):
            tickers += tks
    if symbol.upper().endswith((".NS", ".BO")) and "INR=X" not in tickers:
        tickers.append("INR=X")
    if not tickers:
        tickers = ["DX-Y.NYB"]
    tickers = _uniq(tickers)[:4]
    materials = _uniq([kw for t in tickers for kw in COMMODITIES[t][1][:1]])

    return {
        "symbol": symbol, "company": company, "people": people, "products": [], "industry": [],
        "materials": materials, "exclude": [], "commodities": tickers, "extra_feeds": [],
        "auto": True, "updated": None,
    }


def _path(symbol: str) -> Path:
    return DATA_DIR / (re.sub(r"[^A-Za-z0-9._^=-]", "_", symbol.upper()) + ".json")


def load(symbol: str, info: dict) -> dict:
    p = _path(symbol)
    if p.exists():
        try:
            prof = json.loads(p.read_text(encoding="utf-8"))
            prof.setdefault("auto", False)
            return validate(symbol, prof)
        except Exception:
            pass
    return default_profile(symbol, info)


def validate(symbol: str, prof: dict) -> dict:
    out = {"symbol": symbol}
    for g in GROUPS + ["exclude"]:
        vals = prof.get(g) or []
        out[g] = _uniq([str(v)[:80] for v in vals if isinstance(v, (str, int, float))])[:15]
    out["commodities"] = [t for t in _uniq(prof.get("commodities") or []) if t in COMMODITIES][:6]
    feeds = [f for f in (prof.get("extra_feeds") or []) if isinstance(f, str) and re.match(r"^https?://", f.strip())]
    out["extra_feeds"] = _uniq(feeds)[:10]
    out["auto"] = bool(prof.get("auto", False))
    out["updated"] = prof.get("updated")
    if not out["company"]:
        out["company"] = [symbol.split(".")[0]]
    return out


def save(symbol: str, prof: dict) -> dict:
    prof = validate(symbol, prof)
    prof["auto"] = False
    prof["updated"] = datetime.now(timezone.utc).isoformat()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _path(symbol).write_text(json.dumps(prof, indent=2, ensure_ascii=False), encoding="utf-8")
    return prof


def reset(symbol: str) -> None:
    p = _path(symbol)
    if p.exists():
        p.unlink()


def catalogue() -> list[dict]:
    return [{"ticker": t, "name": v[0]} for t, v in COMMODITIES.items()]
