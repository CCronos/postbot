#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Snapshot en vivo: para cada cuenta trackeada, mira sus periodos activos en
xtracker, proyecta el conteo final con el modelo calibrado (model.py +
sobre-dispersion de backtest.py), lo compara contra los precios reales de los
buckets en Polymarket, y calcula EV/Kelly por bucket.

Misma trampa que ya documentaron en weatherbot con favoritos_bot: un bucket
con precio muy bajo (ej. $0.01) puede mostrar un EV% enorme que no es
ejecutable en tamano real (el book no tiene profundidad, y el edge "gigante"
suele ser ruido del modelo, no una oportunidad real). Se aplican los mismos
tiers de confianza (escalable/moderado/cautela) por precio, no solo un piso
duro, para no repetir ese error desde el dia 1.
"""
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from model import load_history, build_rate_profile, expected_remaining, project_total_calibrated, bucket_probability_normal

ROOT = Path(__file__).resolve().parent
XTRACKER = "https://xtracker.polymarket.com/api"
GAMMA = "https://gamma-api.polymarket.com"

KELLY_FRACTION = 0.25
MIN_EV = 0.05
CONF_TIERS = {"escalable": 0.20, "moderado": 0.10}  # precio minimo por tier


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "postbot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def confianza_tier(price):
    if price >= CONF_TIERS["escalable"]:
        return "escalable"
    if price >= CONF_TIERS["moderado"]:
        return "moderado"
    return "cautela"


def parse_bucket(market):
    """(low, high|None) desde groupItemTitle tipo '<20', '20-39', '500+'."""
    title = (market.get("groupItemTitle") or "").strip()
    m = re.match(r"^(\d+)\+$", title)
    if m:
        return int(m.group(1)), None
    m = re.match(r"^<\s*(\d+)$", title)
    if m:
        return 0, int(m.group(1)) - 1
    m = re.match(r"^(\d+)\s*-\s*(\d+)$", title)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def slug_from_link(link):
    return link.rstrip("/").rsplit("/", 1)[-1]


def load_overdispersion():
    path = ROOT / "data" / "backtest_results.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {r["handle"]: r["overdispersion"] for r in rows}


def snapshot_tracking(handle, profile, overdispersion, tracking, now):
    start = datetime.fromisoformat(tracking["startDate"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(tracking["endDate"].replace("Z", "+00:00"))
    if now >= end:
        return None  # ya termino, nada que operar

    try:
        stats = fetch(f"{XTRACKER}/trackings/{tracking['id']}?includeStats=true")["data"]["stats"]
    except Exception as e:
        return {"error": f"xtracker: {e}"}
    current = stats["cumulative"]

    lam = expected_remaining(profile, now, end)
    mean, std = project_total_calibrated(current, lam, overdispersion)

    slug = slug_from_link(tracking["marketLink"])
    try:
        events = fetch(f"{GAMMA}/events?slug={slug}")
    except Exception as e:
        return {"error": f"gamma: {e}"}
    if not events:
        return {"error": "mercado no encontrado en Gamma (puede no existir todavia)"}
    event = events[0]

    picks = []
    for m in event.get("markets", []):
        if m.get("closed"):
            continue
        low, high = parse_bucket(m)
        if low is None:
            continue
        try:
            outcome_prices = json.loads(m["outcomePrices"])
            price_yes = float(outcome_prices[0])
        except (KeyError, ValueError, IndexError):
            continue
        if price_yes <= 0.001 or price_yes >= 0.999:
            continue  # ya resuelto de facto, sin valor operar

        prob = bucket_probability_normal(mean, std, low, high)
        edge = prob - price_yes
        ev_pct = edge / price_yes if price_yes > 0 else 0
        if ev_pct < MIN_EV:
            continue
        kelly = max(0.0, (prob - price_yes) / (1 - price_yes)) * KELLY_FRACTION if price_yes < 1 else 0

        picks.append({
            "bucket": m.get("groupItemTitle"),
            "low": low, "high": high,
            "price": round(price_yes, 4),
            "model_prob": round(prob, 4),
            "ev_pct": round(ev_pct * 100, 1),
            "kelly_pct": round(kelly * 100, 2),
            "confianza": confianza_tier(price_yes),
            "volume": m.get("volumeNum", 0),
        })

    picks.sort(key=lambda p: p["ev_pct"], reverse=True)
    return {
        "title": tracking["title"],
        "marketLink": tracking["marketLink"],
        "current_count": current,
        "hours_elapsed": round(stats["daysElapsed"] * 24, 1) if "daysElapsed" in stats else None,
        "percent_complete": stats.get("percentComplete"),
        "projected_mean": round(mean, 1),
        "projected_std": round(std, 1),
        "picks": picks,
    }


def main():
    overdispersion_by_handle = load_overdispersion()
    users = fetch(f"{XTRACKER}/users")["data"]
    now = datetime.now(timezone.utc)

    report = {}
    for u in users:
        handle = u["handle"]
        if not u.get("trackings"):
            continue
        history = load_history(handle)
        profile = build_rate_profile(history["posts"])
        overdispersion = overdispersion_by_handle.get(handle, 5.0)  # default conservador si no hay backtest

        print(f"\n=== {handle} (sobre-disp. {overdispersion}x) ===")
        account_results = []
        for tracking in u["trackings"]:
            snap = snapshot_tracking(handle, profile, overdispersion, tracking, now)
            if snap is None:
                continue
            if "error" in snap:
                print(f"  {tracking['title']}: {snap['error']}")
                continue
            account_results.append(snap)
            print(f"  {snap['title']}")
            print(f"    llevaba {snap['current_count']} · proyeccion {snap['projected_mean']} ± {snap['projected_std']}")
            if not snap["picks"]:
                print(f"    sin picks con EV >= {MIN_EV*100:.0f}%")
            for p in snap["picks"][:3]:
                print(f"    {p['bucket']:>10s} @ ${p['price']:.3f} | modelo {p['model_prob']*100:.0f}% | "
                      f"EV +{p['ev_pct']:.0f}% | Kelly {p['kelly_pct']:.1f}% | {p['confianza']} | vol ${p['volume']:,.0f}")
            time.sleep(0.2)
        report[handle] = account_results

    out_path = ROOT / "data" / "live_snapshot.json"
    out_path.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "accounts": report,
    }, indent=2), encoding="utf-8")
    print(f"\nGuardado en {out_path}")


if __name__ == "__main__":
    main()
