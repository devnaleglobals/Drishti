"""Source credibility tiers used to rank evidence.

official    exchange and regulator filings
mainstream  established financial and national media (the 'mainstream media' that rumour verification focuses on)
online      other online news sites and aggregators
social      forums, video and social posts
"""
from __future__ import annotations

from urllib.parse import urlparse

TIERS = {
    "official": {"label": "Official", "weight": 1.0},
    "mainstream": {"label": "Mainstream media", "weight": 0.85},
    "online": {"label": "Other online news", "weight": 0.6},
    "social": {"label": "Social / forum", "weight": 0.4},
}

_OFFICIAL = ("nseindia.com", "bseindia.com", "sebi.gov.in", "rbi.org.in", "nse filing", "bse filing", "sebi")
_MAINSTREAM = (
    # domains
    "economictimes", "livemint", "business-standard", "moneycontrol", "thehindubusinessline", "financialexpress",
    "reuters", "bloomberg", "cnbctv18", "ndtvprofit", "ndtv.com", "businesstoday", "timesofindia", "hindustantimes",
    "thehindu", "indianexpress", "zeebiz", "fortuneindia", "forbesindia", "cnbc.com", "wsj.com", "ft.com",
    "marketwatch", "outlookbusiness", "news18", "theprint", "deccanherald", "telegraphindia",
    # outlet names as feeds report them
    "economic times", "mint", "business standard", "moneycontrol", "businessline", "hindu", "financial express",
    "reuters", "bloomberg", "cnbc", "ndtv", "business today", "times of india", "hindustan times", "indian express",
    "zee business", "et now", "fortune india", "forbes india", "wall street journal", "financial times",
)


def tier_of(source: str, url: str = "", kind: str = "news") -> str:
    if kind in ("forum", "video", "social"):
        return "social"
    text = f"{(source or '').lower()} {urlparse(url or '').netloc.lower()}"
    if kind == "filing" or any(k in text for k in _OFFICIAL):
        return "official"
    if any(k in text for k in _MAINSTREAM):
        return "mainstream"
    return "online"
