#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Modelo de proyeccion de conteo de posts. Mismo espiritu que la curva
empirica de weatherbot (husky_query.py): "dado lo que ya paso, cuanto falta
segun el patron historico" - aca en vez de temperatura es cantidad de posts.

Perfil de tasa: promedio de posts por hora, condicionado a (dia de la semana,
hora del dia UTC) - 168 baldes. Captura tanto el patron diario (mas activo de
dia) como semanal (mas o menos activo ciertos dias).

Proyeccion: proceso de Poisson con tasa variable en el tiempo. El conteo
actual ya es un hecho conocido (no tiene incertidumbre); lo unico incierto es
cuanto falta por venir en las horas que quedan del periodo. Sumar Poissons
independientes da otro Poisson con lambda = suma de las tasas esperadas por
hora restante - es el modelo mas simple y defendible para datos de conteo
tipo "llegadas en el tiempo", sin inventar una forma de curva a mano.
"""
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_history(handle):
    path = ROOT / "data" / "history" / f"{handle}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    posts = data["posts"]
    for p in posts:
        p["_dt"] = datetime.fromisoformat(p["createdAt"].replace("Z", "+00:00"))
    return data


def build_rate_profile(posts, min_weeks=2):
    """168 baldes (dow*24+hour) -> tasa promedio de posts por hora.

    Se cuenta cuantas veces aparece cada balde en el rango de datos disponible
    (no siempre es un numero entero de semanas) para promediar bien, en vez de
    asumir que todas las semanas estan completas.
    """
    if not posts:
        return [0.0] * 168
    counts = defaultdict(int)
    for p in posts:
        dt = p["_dt"]
        bucket = dt.weekday() * 24 + dt.hour
        counts[bucket] += 1

    start = posts[0]["_dt"].replace(minute=0, second=0, microsecond=0)
    end = posts[-1]["_dt"].replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    occurrences = defaultdict(int)
    cur = start
    while cur < end:
        bucket = cur.weekday() * 24 + cur.hour
        occurrences[bucket] += 1
        cur += timedelta(hours=1)

    profile = []
    overall_mean = len(posts) / max(1, (end - start).total_seconds() / 3600)
    for b in range(168):
        occ = occurrences.get(b, 0)
        if occ >= min_weeks:
            profile.append(counts.get(b, 0) / occ)
        else:
            profile.append(overall_mean)  # sin muestra suficiente, cae al promedio general
    return profile


def expected_remaining(profile, from_dt, to_dt):
    """Suma la tasa esperada (posts/hora) del perfil sobre cada hora entre
    from_dt y to_dt (exclusive del final), fraccionando la primera/ultima hora
    si no caen en el borde exacto."""
    if from_dt >= to_dt:
        return 0.0
    total = 0.0
    cur = from_dt.replace(minute=0, second=0, microsecond=0)
    while cur < to_dt:
        nxt = cur + timedelta(hours=1)
        bucket = cur.weekday() * 24 + cur.hour
        rate = profile[bucket]
        overlap_start = max(cur, from_dt)
        overlap_end = min(nxt, to_dt)
        frac = (overlap_end - overlap_start).total_seconds() / 3600
        total += rate * max(0.0, frac)
        cur = nxt
    return total


def poisson_pmf(k, lam):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    if k < 0:
        return 0.0
    log_pmf = k * math.log(lam) - lam - math.lgamma(k + 1)
    return math.exp(log_pmf)


def bucket_probability(current_count, lam, low, high):
    """P(total final en [low, high]) dado que ya va 'current_count' y falta
    un Poisson(lam) por venir. high=None => bucket abierto hacia arriba."""
    if current_count > (high if high is not None else float("inf")):
        return 0.0
    lo_k = max(0, low - current_count)
    if high is None:
        # cola superior: 1 - CDF(lo_k - 1) via complemento, sumando hasta
        # convergencia (lam finito, la cola decae rapido mas alla de lam+10*sqrt(lam))
        cdf_before = sum(poisson_pmf(k, lam) for k in range(0, lo_k))
        return max(0.0, 1.0 - cdf_before)
    hi_k = high - current_count
    return sum(poisson_pmf(k, lam) for k in range(lo_k, hi_k + 1))


def project_total(current_count, lam):
    """Punto de estimacion (media) y desvio estandar del total final,
    Poisson puro (sin calibrar) - referencia, no usar para EV real."""
    mean = current_count + lam
    std = math.sqrt(lam) if lam > 0 else 0.0
    return mean, std


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def project_total_calibrated(current_count, lam, overdispersion):
    """Igual que project_total pero con la varianza inflada por el factor de
    sobre-dispersion medido en backtest.py (postear es 'a rafagas', no un
    proceso de Poisson limpio - ver backtest para el numero real por cuenta).
    Con lam*overdispersion tipicamente grande, se aproxima por Normal en vez
    de Poisson exacto (mas simple y suficientemente preciso aca)."""
    mean = current_count + lam
    std = math.sqrt(max(lam, 0.01) * max(1.0, overdispersion))
    return mean, std


def bucket_probability_normal(mean, std, low, high):
    """P(total final en [low, high]) aproximando la distribucion final como
    Normal(mean, std). high=None => bucket abierto hacia arriba (cola)."""
    if std <= 0:
        return 1.0 if (low <= mean <= (high if high is not None else mean)) else 0.0
    lo_z = (low - 0.5 - mean) / std  # correccion de continuidad, es una var discreta
    if high is None:
        return max(0.0, 1.0 - norm_cdf(lo_z))
    hi_z = (high + 0.5 - mean) / std
    return max(0.0, norm_cdf(hi_z) - norm_cdf(lo_z))
