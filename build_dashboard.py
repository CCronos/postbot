#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arma dist/index.html: el dashboard de CHATTER, con una seccion por cada
cuenta trackeada. Lee data/live_snapshot.json (live_snapshot.py) y
data/backtest_results.json (backtest.py) - hay que correr esos dos primero.

Cero dependencia de ningun dato/tema de weatherbot a proposito (repo
separado, pedido explicito del usuario 2026-08-06).
"""
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
DIST.mkdir(exist_ok=True)

DISPLAY_NAMES = {
    "elonmusk": "Elon Musk", "realDonaldTrump": "Donald Trump",
    "ZelenskyyUa": "Volodymyr Zelenskyy", "tedcruz": "Ted Cruz",
    "WhiteHouse": "The White House", "cz_binance": "CZ (Binance)",
    "khamenei_ir": "Ali Khamenei", "NYCMayor": "Zohran Mamdani (NYC Mayor)",
    "Cobratate": "Andrew Tate",
}


def build_account_data():
    live = json.loads((ROOT / "data" / "live_snapshot.json").read_text(encoding="utf-8"))
    backtest_rows = json.loads((ROOT / "data" / "backtest_results.json").read_text(encoding="utf-8"))
    backtest_by_handle = {r["handle"]: r for r in backtest_rows}
    # daily_summary.json y summary.json los genera el ciclo lento
    # (download_history.py, cada 6h) - el ciclo rapido (cada 5 min) solo lee
    # estos dos archivos livianos, nunca descarga el historial completo.
    daily_by_handle = json.loads((ROOT / "data" / "daily_summary.json").read_text(encoding="utf-8"))
    totals_by_handle = json.loads((ROOT / "data" / "summary.json").read_text(encoding="utf-8"))

    accounts = []
    for handle, trackings in live["accounts"].items():
        accounts.append({
            "handle": handle,
            "name": DISPLAY_NAMES.get(handle, handle),
            "trackings": trackings,
            "backtest": backtest_by_handle.get(handle),
            "daily": daily_by_handle.get(handle, []),
            "total_posts": totals_by_handle.get(handle, 0),
        })
    accounts.sort(key=lambda a: -a["total_posts"])
    return accounts, live["generated_at"]


def best_picks_summary(accounts):
    rows = []
    for a in accounts:
        for t in a["trackings"]:
            for p in t["picks"]:
                if p["confianza"] == "cautela":
                    continue
                rows.append({**p, "account": a["name"], "handle": a["handle"], "period": t["title"]})
    rows.sort(key=lambda r: -r["ev_pct"])
    return rows[:12]


TIER_LABEL = {"escalable": "escalable", "moderado": "moderado", "cautela": "cautela (no operable)"}


def render_picks_table(picks, show_account=False):
    if not picks:
        return '<div class="empty">Sin picks con EV real ahora mismo.</div>'
    head_acc = "<th>Cuenta</th>" if show_account else ""
    rows = ""
    for p in picks:
        acc_cell = f'<td>{p["account"]}</td>' if show_account else ""
        rows += (f'<tr class="tier-{p["confianza"]}">{acc_cell}'
                  f'<td class="mono">{p["bucket"]}</td>'
                  f'<td class="mono">${p["price"]:.3f}</td>'
                  f'<td class="mono">{p["model_prob"]*100:.0f}%</td>'
                  f'<td class="mono ev">+{p["ev_pct"]:.0f}%</td>'
                  f'<td class="mono">{p["kelly_pct"]:.1f}%</td>'
                  f'<td><span class="tag tag-{p["confianza"]}">{TIER_LABEL[p["confianza"]]}</span></td>'
                  f'<td class="mono dim">${p["volume"]:,.0f}</td></tr>')
    return (f'<table class="picks"><thead><tr>{head_acc}'
            '<th>Bucket</th><th>Precio</th><th>Modelo</th><th>EV</th><th>Kelly</th><th>Confianza</th><th>Vol</th>'
            f'</tr></thead><tbody>{rows}</tbody></table>')


def render_account_section(a):
    bt = a["backtest"]
    bt_html = ""
    if bt:
        bt_html = (f'<div class="bt-row">'
                   f'<span>MAE @75% del periodo: <b>{bt["mae_pct_75"]}%</b></span>'
                   f'<span>Sobre-dispersion: <b>{bt["overdispersion"]}x</b></span>'
                   f'<span>{bt["n_windows"]} ventanas historicas analizadas</span>'
                   f'</div>')

    trackings_html = ""
    for t in a["trackings"]:
        pct = t.get("percent_complete") or 0
        trackings_html += f'''
        <div class="tracking">
          <div class="tracking-head">
            <a href="{t['marketLink']}" target="_blank" rel="noopener">{t['title']}</a>
            <span class="dim mono">{pct:.0f}% del periodo</span>
          </div>
          <div class="bar"><div class="bar-fill" style="width:{min(100,pct)}%"></div></div>
          <div class="tracking-stats mono dim">
            lleva {t['current_count']} · proyeccion {t['projected_mean']} &plusmn; {t['projected_std']}
          </div>
          {render_picks_table(t['picks'])}
        </div>'''

    chart_id = f"chart-{a['handle']}"
    daily_json = json.dumps(a["daily"])
    return f'''
    <section class="account" id="{a['handle']}">
      <div class="account-head">
        <h2>{a['name']}</h2>
        <span class="dim mono">@{a['handle']} &middot; {a['total_posts']:,} posts registrados</span>
      </div>
      {bt_html}
      <div class="chart-wrap"><canvas id="{chart_id}" data-daily='{daily_json}'></canvas></div>
      {trackings_html}
    </section>'''


def main():
    accounts, generated_at = build_account_data()
    top_picks = best_picks_summary(accounts)
    gen_dt = datetime.fromisoformat(generated_at)

    sections = "\n".join(render_account_section(a) for a in accounts)
    nav_links = "\n".join(f'<a href="#{a["handle"]}">{a["name"]}</a>' for a in accounts)

    html = HTML_TEMPLATE.format(
        generated_at=gen_dt.strftime("%Y-%m-%d %H:%M UTC"),
        n_accounts=len(accounts),
        top_picks_table=render_picks_table(top_picks, show_account=True),
        nav_links=nav_links,
        sections=sections,
    )
    (DIST / "index.html").write_text(html, encoding="utf-8")
    print(f"generado: {DIST / 'index.html'} ({len(html)/1024:.0f} KB)")


HTML_TEMPLATE = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CHATTER &middot; conteo de posts como mercado</title>
<style>
  :root {{
    --bg: #0a0a0c; --panel: #131317; --panel-2: #1a1a20; --line: #2a2a33;
    --text: #eceef2; --text-dim: #8b8d98; --text-faint: #55575f;
    --accent: #ff3366; --accent-soft: rgba(255,51,102,0.13); --accent-dim: #7a1a30;
    --cyan: #2dd4ee; --cyan-soft: rgba(45,212,238,0.12);
    --good: #34d399; --warn: #fbbf24; --bad: #f87171;
    --mono: ui-monospace, "Cascadia Code", "JetBrains Mono", Consolas, monospace;
    --sans: ui-sans-serif, "Segoe UI", Arial, sans-serif;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--text); font-family: var(--sans); }}
  body {{ font-size: 14px; line-height: 1.5; }}
  a {{ color: var(--cyan); }}
  .mono {{ font-family: var(--mono); font-variant-numeric: tabular-nums; }}
  .dim {{ color: var(--text-dim); }}
  .wrap {{ max-width: 1180px; margin: 0 auto; padding: 0 20px 60px; }}

  header.hero {{
    border-bottom: 1px solid var(--line);
    background: linear-gradient(180deg, #101014 0%, var(--bg) 100%);
    padding: 32px 20px 22px;
    margin-bottom: 20px;
  }}
  .hero-inner {{ max-width: 1180px; margin: 0 auto; }}
  .wordmark {{
    font-family: var(--mono); font-weight: 700; font-size: 26px; letter-spacing: 0.12em;
    color: var(--text);
  }}
  .wordmark .dot {{ color: var(--accent); }}
  .tagline {{ color: var(--text-dim); font-size: 13px; margin-top: 6px; max-width: 62ch; }}
  .meta {{ font-family: var(--mono); font-size: 11px; color: var(--text-faint); margin-top: 10px; }}

  nav.jump {{ display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 16px; }}
  nav.jump a {{
    font-family: var(--mono); font-size: 11.5px; color: var(--text-dim); text-decoration: none;
    border: 1px solid var(--line); padding: 4px 10px; border-radius: 3px;
  }}
  nav.jump a:hover {{ color: var(--cyan); border-color: var(--cyan); }}

  .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 4px; padding: 18px 20px; margin-bottom: 20px; }}
  h2 {{ font-family: var(--mono); font-size: 15px; letter-spacing: 0.02em; margin: 0; color: var(--text); }}
  h3.section-title {{ font-family: var(--mono); font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-faint); margin: 0 0 12px; }}

  table.picks {{ width: 100%; border-collapse: collapse; font-size: 12.5px; margin-top: 10px; }}
  table.picks th {{ text-align: left; font-size: 10px; letter-spacing: 0.05em; text-transform: uppercase; color: var(--text-faint); padding: 6px 8px; border-bottom: 1px solid var(--line); }}
  table.picks td {{ padding: 7px 8px; border-bottom: 1px solid var(--line); }}
  table.picks .ev {{ color: var(--good); font-weight: 700; }}
  .tag {{ font-size: 10px; padding: 2px 7px; border-radius: 2px; font-family: var(--mono); white-space: nowrap; }}
  .tag-escalable {{ background: rgba(52,211,153,0.14); color: var(--good); }}
  .tag-moderado {{ background: rgba(251,191,36,0.14); color: var(--warn); }}
  .tag-cautela {{ background: rgba(248,113,113,0.10); color: var(--text-faint); }}
  tr.tier-cautela {{ opacity: 0.55; }}
  .empty {{ color: var(--text-faint); font-size: 12.5px; padding: 8px 0; }}

  .account {{ border-top: 1px solid var(--line); padding-top: 26px; margin-top: 26px; }}
  .account-head {{ display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 6px 14px; margin-bottom: 10px; }}
  .bt-row {{ display: flex; gap: 18px; flex-wrap: wrap; font-size: 11.5px; color: var(--text-dim); margin-bottom: 14px; font-family: var(--mono); }}
  .bt-row b {{ color: var(--text); }}

  .chart-wrap {{ margin-bottom: 16px; }}
  canvas {{ width: 100%; height: 110px; display: block; }}

  .tracking {{ background: var(--panel-2); border: 1px solid var(--line); border-radius: 4px; padding: 14px 16px; margin-bottom: 12px; }}
  .tracking-head {{ display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 6px; }}
  .tracking-head a {{ text-decoration: none; font-weight: 600; }}
  .tracking-head a:hover {{ text-decoration: underline; }}
  .bar {{ height: 4px; background: var(--line); border-radius: 2px; margin: 8px 0; overflow: hidden; }}
  .bar-fill {{ height: 100%; background: var(--accent); }}
  .tracking-stats {{ font-size: 11.5px; margin-bottom: 6px; }}

  footer {{ margin-top: 30px; padding-top: 16px; border-top: 1px solid var(--line); font-size: 11px; color: var(--text-faint); font-family: var(--mono); line-height: 1.7; }}
</style>
</head>
<body>
<header class="hero">
  <div class="hero-inner">
    <div class="wordmark">CHATTER<span class="dot">.</span></div>
    <div class="tagline">Mercados de conteo de posts en Polymarket (Elon, Trump, y otras {n_accounts} figuras publicas) &mdash; modelo de tasa de posteo calibrado contra el historial real, cruzado contra precios reales del libro de ordenes.</div>
    <div class="meta">ultima actualizacion: {generated_at}</div>
    <nav class="jump">{nav_links}</nav>
  </div>
</header>

<div class="wrap">
  <div class="panel">
    <h3 class="section-title">Mejores picks ahora (solo escalable/moderado &mdash; cautela se excluye a proposito, EV inflado no ejecutable)</h3>
    {top_picks_table}
  </div>

  {sections}

  <footer>
    Fuente de conteo: xtracker.polymarket.com (la misma que usa Polymarket para resolver estos mercados).<br>
    Modelo: tasa de posteo historica por hora/dia de la semana + proyeccion calibrada con la sobre-dispersion real medida en backtest.<br>
    "cautela" = precio &lt;$0.10, EV matematicamente inflado y no ejecutable en tamano real (ver nota en cada seccion). No es consejo financiero.
  </footer>
</div>

<script>
(function() {{
  document.querySelectorAll("canvas[data-daily]").forEach(function(canvas) {{
    var daily = JSON.parse(canvas.getAttribute("data-daily"));
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth || 900, h = 110;
    canvas.width = w * dpr; canvas.height = h * dpr;
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (!daily.length) return;
    var max = Math.max.apply(null, daily.map(function(d) {{ return d[1]; }}), 1);
    var bw = w / daily.length;
    daily.forEach(function(d, i) {{
      var bh = (d[1] / max) * (h - 16);
      ctx.fillStyle = "rgba(255,51,102,0.55)";
      ctx.fillRect(i * bw + 1, h - bh, Math.max(1, bw - 2), bh);
    }});
  }});
}})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
