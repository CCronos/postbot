#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Baja el historial completo de posts de las cuentas trackeadas por
xtracker.polymarket.com (la fuente oficial que usa Polymarket para resolver
los mercados de "# tweets/posts"). Sin esto no hay con que construir la curva
de tasa de posteo.

xtracker empieza a trackear a cada cuenta en una fecha distinta (campo
`createdAt` del usuario) - se pide mes a mes desde esa fecha hasta hoy y se
deduplica por id, para no asumir un rango fijo que puede quedar corto o
pedir de mas.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request

BASE = "https://xtracker.polymarket.com/api"
ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "data" / "history"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch(url):
    req = Request(url, headers={"User-Agent": "postbot/1.0"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def month_ranges(start, end):
    """Genera [ (inicio_mes, fin_mes) ... ] cubriendo start..end, en UTC."""
    cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    out = []
    while cur < end:
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1)
        else:
            nxt = cur.replace(month=cur.month + 1)
        out.append((cur, min(nxt, end)))
        cur = nxt
    return out


def download_user(handle, created_at_str):
    start = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    posts_by_id = {}
    for m_start, m_end in month_ranges(start, now):
        url = (f"{BASE}/users/{handle}/posts"
               f"?startDate={m_start.isoformat().replace('+00:00', 'Z')}"
               f"&endDate={m_end.isoformat().replace('+00:00', 'Z')}")
        try:
            resp = fetch(url)
        except Exception as e:
            print(f"    error en {m_start.date()}..{m_end.date()}: {e}")
            continue
        for p in resp.get("data", []):
            posts_by_id[p["id"]] = p
        time.sleep(0.3)  # no hay rate limit documentado, pero no hay razon para forzarlo
    return sorted(posts_by_id.values(), key=lambda p: p["createdAt"])


def main():
    users = fetch(f"{BASE}/users")["data"]
    print(f"{len(users)} cuentas trackeadas")
    summary = {}
    for u in users:
        handle = u["handle"]
        print(f"  {handle}: bajando desde {u['createdAt'][:10]}...")
        posts = download_user(handle, u["createdAt"])
        out_path = OUT_DIR / f"{handle}.json"
        out_path.write_text(json.dumps({
            "handle": handle,
            "platform": u["platform"],
            "trackedSince": u["createdAt"],
            "posts": posts,
        }, separators=(",", ":")), encoding="utf-8")
        summary[handle] = len(posts)
        print(f"    {len(posts)} posts -> {out_path.name} ({out_path.stat().st_size/1024:.0f} KB)")

    (ROOT / "data" / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("listo:", summary)


if __name__ == "__main__":
    main()
