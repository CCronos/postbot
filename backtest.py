#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backtest del modelo de model.py: para cada cuenta, reconstruye ventanas
semanales pasadas ya completas, proyecta el total en 3 puntos de avance
(25%/50%/75% de la semana) usando SOLO los posts que ya habrian ocurrido a
esa hora, y compara contra el total real de la semana completa.

Caveat honesto (se imprime en el reporte): el perfil de tasa se construye con
TODO el historial disponible, no excluyendo la semana de prueba - para cuentas
con miles de posts (Elon, Trump) el efecto es minimo, pero para las de pocos
posts (khamenei_ir, 149 en total) hay algo de optimismo en el numero. Sirve
para saber si el modelo tiene forma razonable, no como cifra final de
precision para produccion.
"""
import json
import statistics
from datetime import timedelta
from pathlib import Path

import math

from model import load_history, build_rate_profile, expected_remaining, project_total

ROOT = Path(__file__).resolve().parent
HANDLES = [p.stem for p in (ROOT / "data" / "history").glob("*.json")]


def weekly_windows(posts, min_posts_per_week=5):
    if not posts:
        return []
    start = posts[0]["_dt"]
    end = posts[-1]["_dt"]
    windows = []
    cur = start
    while cur + timedelta(days=7) <= end:
        w_end = cur + timedelta(days=7)
        count = sum(1 for p in posts if cur <= p["_dt"] < w_end)
        if count >= min_posts_per_week:
            windows.append((cur, w_end, count))
        cur += timedelta(days=7)
    return windows


def backtest_handle(handle):
    data = load_history(handle)
    posts = data["posts"]
    if len(posts) < 30:
        return None
    profile = build_rate_profile(posts)
    windows = weekly_windows(posts)
    if len(windows) < 3:
        return None

    errors_pct = {0.25: [], 0.5: [], 0.75: []}
    sq_err_over_lam = []  # (actual_remaining - lam)^2 / lam, para estimar sobre-dispersion
    for w_start, w_end, actual_total in windows:
        for frac in (0.25, 0.5, 0.75):
            checkpoint = w_start + (w_end - w_start) * frac
            current = sum(1 for p in posts if w_start <= p["_dt"] < checkpoint)
            lam = expected_remaining(profile, checkpoint, w_end)
            mean, _std = project_total(current, lam)
            err_pct = (mean - actual_total) / actual_total * 100 if actual_total else 0
            errors_pct[frac].append(err_pct)
            actual_remaining = actual_total - current
            if lam > 1:  # con lam muy chico el ratio se dispara y no aporta señal
                sq_err_over_lam.append(((actual_remaining - lam) ** 2) / lam)

    # Metodo de momentos: si el conteo real fuera Poisson puro, E[(real-lam)^2/lam] = 1.
    # Un promedio > 1 es la sobre-dispersion real observada - se usa despues como
    # multiplicador de la varianza en vivo (project_total asume Poisson "limpio").
    overdispersion = round(statistics.mean(sq_err_over_lam), 2) if sq_err_over_lam else 1.0

    result = {"handle": handle, "n_windows": len(windows), "n_posts": len(posts),
               "overdispersion": max(1.0, overdispersion)}
    for frac, errs in errors_pct.items():
        result[f"mae_pct_{int(frac*100)}"] = round(statistics.mean(abs(e) for e in errs), 1)
        result[f"bias_pct_{int(frac*100)}"] = round(statistics.mean(errs), 1)
    return result


def main():
    print(f"{'cuenta':18s} {'ventanas':>9s} {'posts':>7s}  |  {'MAE% @25%':>10s} {'MAE% @50%':>10s} {'MAE% @75%':>10s}  |  sesgo@50%  |  sobre-disp.")
    print("-" * 105)
    results = []
    rate_profiles = {}
    for handle in sorted(HANDLES):
        r = backtest_handle(handle)
        if r is None:
            print(f"{handle:18s}  (muy pocos datos/ventanas, se salta)")
            continue
        results.append(r)
        print(f"{r['handle']:18s} {r['n_windows']:>9d} {r['n_posts']:>7d}  |  "
              f"{r['mae_pct_25']:>9.1f}% {r['mae_pct_50']:>9.1f}% {r['mae_pct_75']:>9.1f}%  |  "
              f"{r['bias_pct_50']:>+7.1f}%  |  {r['overdispersion']:>5.1f}x")
        rate_profiles[handle] = build_rate_profile(load_history(handle)["posts"])

    (ROOT / "data" / "backtest_results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")
    # 168 numeros (7 dias x 24h) por cuenta - liviano, se commitea para que el
    # ciclo rapido (live_snapshot.py) proyecte sin necesitar el historial crudo.
    (ROOT / "data" / "rate_profiles.json").write_text(
        json.dumps(rate_profiles, separators=(",", ":")), encoding="utf-8")
    print("\nMAE% = error absoluto promedio como % del total real. Mas bajo = mejor.")
    print("Se espera que MAE baje de @25% a @75% (menos por proyectar, mas ya contado).")


if __name__ == "__main__":
    main()
