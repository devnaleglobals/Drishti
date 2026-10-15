"""App-wide source settings, edited from the Keywords and sources lens.

Stored in DATA_DIR/settings.json:
  disabled  list of source ids switched off in the app
  feeds     publisher feed list {"IN": [[name, url], ...], "GLOBAL": [...]}, or null to use feeds.json
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
PATH = Path(os.getenv("DATA_DIR", BASE / "data")) / "settings.json"
_lock = threading.Lock()
_mem = {"mtime": None, "data": None}


def _default():
    return {"disabled": [], "feeds": None, "updated": None}


def load() -> dict:
    with _lock:
        try:
            if not PATH.exists():
                return _default()
            mtime = PATH.stat().st_mtime
            if _mem["mtime"] == mtime and _mem["data"] is not None:
                return _mem["data"]
            data = {**_default(), **json.loads(PATH.read_text(encoding="utf-8"))}
            _mem.update(mtime=mtime, data=data)
            return data
        except Exception:
            return _default()


def _clean_feeds(feeds):
    if feeds is None:
        return None
    out = {}
    for region in ("IN", "GLOBAL"):
        rows = []
        for row in (feeds or {}).get(region, []) or []:
            if isinstance(row, (list, tuple)) and len(row) == 2:
                name, url = str(row[0]).strip()[:60], str(row[1]).strip()
                if name and re.match(r"^https?://", url):
                    rows.append([name, url])
        out[region] = rows[:40]
    return out


def save(disabled, feeds) -> dict:
    data = {
        "disabled": sorted({str(x) for x in (disabled or []) if re.match(r"^[a-z_]+$", str(x))}),
        "feeds": _clean_feeds(feeds),
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    with _lock:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _mem.update(mtime=None, data=None)
    return data


def version() -> str:
    return load().get("updated") or "default"
