#!/usr/bin/env python
"""Generate the SVG charts from the benchmark results. No dependencies.

Each chart is rendered in both a light and a dark theme:

  parse_validate.svg      / parse_validate-dark.svg : /api/create  @ concurrency=1
  concurrency.svg         / concurrency-dark.svg    : /api/iojob   @ concurrency=50

The default palette is monochrome (black / dark gray / light gray), inverted for
the dark theme. Bar colors are overridable (applied to both themes):
  python make_charts.py --ninja '#111111' --flask '#4285F4' --drf '#34A853' --adrf '#1E8E3E'
"""
import argparse
import json
import math
import os

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "charts")


def nice_step(maxv, nticks=5):
    raw = maxv / nticks
    mag = 10 ** math.floor(math.log10(raw))
    f = raw / mag
    nf = 1 if f <= 1 else 2 if f <= 2 else 5 if f <= 5 else 10
    return nf * mag


def axis(maxv, nticks=5):
    step = nice_step(maxv, nticks)
    top = math.ceil(maxv / step) * step
    ticks = [i * step for i in range(int(top / step) + 1)]
    return top, ticks


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def grouped_bar_svg(title, subtitle, groups, series, values, theme,
                    ylabel="requests / second", xlabel=None, width=820, height=460,
                    mb=76, value_labels=True, group_label_lines=None, legend_labels=None):
    """groups: [label,...]  series: [name,...]  values: {group: {series: val}}
    theme: a THEMES entry (bg / text colors + per-series bar palette).
    xlabel: optional x-axis title under the group labels.
    legend_labels: optional {series: label} override for the legend (else LEGEND)."""
    bars = theme["bars"]
    ml, mr, mt = 78, 24, 64
    pw, ph = width - ml - mr, height - mt - mb
    maxv = max(v for g in values.values() for v in g.values())
    ytop, ticks = axis(maxv)
    x0, y0 = ml, mt + ph  # bottom-left of plot

    def yp(v):
        return mt + ph - (v / ytop) * ph

    s = []
    s.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}" font-family="-apple-system,Segoe UI,Roboto,sans-serif">')
    s.append(f'<rect width="{width}" height="{height}" fill="{theme["bg"]}"/>')
    s.append(f'<text x="{ml}" y="28" font-size="19" font-weight="700" fill="{theme["title"]}">{esc(title)}</text>')
    if subtitle:
        s.append(f'<text x="{ml}" y="48" font-size="13" fill="{theme["subtitle"]}">{esc(subtitle)}</text>')

    # y gridlines + labels
    for t in ticks:
        y = yp(t)
        s.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{ml+pw}" y2="{y:.1f}" stroke="{theme["grid"]}"/>')
        s.append(f'<text x="{ml-8}" y="{y+4:.1f}" font-size="11" fill="{theme["tick"]}" text-anchor="end">{int(t)}</text>')
    s.append(f'<text x="16" y="{mt+ph/2:.0f}" font-size="12" fill="{theme["axis_label"]}" '
             f'transform="rotate(-90 16 {mt+ph/2:.0f})" text-anchor="middle">{esc(ylabel)}</text>')

    ng, nse = len(groups), len(series)
    gw = pw / ng
    inner = gw * 0.78
    bw = inner / nse
    for gi, g in enumerate(groups):
        gx = ml + gi * gw + (gw - inner) / 2
        for si, name in enumerate(series):
            v = values[g].get(name)
            if v is None:
                continue
            bx = gx + si * bw
            by = yp(v)
            bh = mt + ph - by
            s.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw-3:.1f}" height="{bh:.1f}" '
                     f'fill="{bars[name]}" rx="1.5"/>')
            if value_labels:
                s.append(f'<text x="{bx+(bw-3)/2:.1f}" y="{by-5:.1f}" font-size="10.5" '
                         f'fill="{theme["value"]}" text-anchor="middle">{int(round(v))}</text>')
        # group label (possibly multi-line)
        lines = (group_label_lines or {}).get(g, [g])
        ly = mt + ph + 20
        for li, line in enumerate(lines):
            s.append(f'<text x="{ml+gi*gw+gw/2:.1f}" y="{ly+li*15:.1f}" font-size="12" '
                     f'fill="{theme["group"]}" text-anchor="middle">{esc(line)}</text>')

    # x-axis title, below the deepest group-label line
    if xlabel:
        maxlines = max(len((group_label_lines or {}).get(g, [g])) for g in groups)
        xy = mt + ph + 20 + maxlines * 15 + 6
        s.append(f'<text x="{ml+pw/2:.1f}" y="{xy:.1f}" font-size="12" '
                 f'fill="{theme["axis_label"]}" text-anchor="middle">{esc(xlabel)}</text>')

    # legend
    lx, lyy = ml, height - 16
    for name in series:
        s.append(f'<rect x="{lx}" y="{lyy-10}" width="13" height="13" fill="{bars[name]}" rx="2"/>')
        label = (legend_labels or LEGEND).get(name, name)
        s.append(f'<text x="{lx+18}" y="{lyy:.0f}" font-size="12" fill="{theme["legend"]}">{esc(label)}</text>')
        lx += 30 + 8 * len(label)
    s.append('</svg>')
    return "\n".join(s)


LEGEND = {
    "ninja": "Django Ninja", "flask": "Flask + marshmallow",
    "drf": "Django REST framework", "adrf": "adrf (async DRF)",
}

# Monochrome palette: ninja=black, flask=dark gray, drf=light gray (inverted for dark).
THEMES = {
    "light": {
        "bg": "#ffffff",
        "title": "#111111",
        "subtitle": "#666666",
        "grid": "#e6e6e6",
        "tick": "#888888",
        "axis_label": "#666666",
        "value": "#333333",
        "group": "#444444",
        "legend": "#333333",
        "bars": {"ninja": "#1a1a1a", "flask": "#808080", "drf": "#bfbfbf", "adrf": "#999999"},
    },
    "dark": {
        "bg": "#0d1117",
        "title": "#e6edf3",
        "subtitle": "#9198a1",
        "grid": "#30363d",
        "tick": "#8b949e",
        "axis_label": "#9198a1",
        "value": "#c9d1d9",
        "group": "#adbac7",
        "legend": "#c9d1d9",
        "bars": {"ninja": "#f0f0f0", "flask": "#9e9e9e", "drf": "#5c5c5c", "adrf": "#7a7a7a"},
    },
}


def chart_parse_validate(theme):
    rm = json.load(open(os.path.join(BASE, "benchmark_results", "results_route_matrix.json")))
    sync = rm["sync (def / gunicorn WSGI)"]
    asyn = rm["async (async def / uvicorn ASGI)"]
    groups = ["sync", "async"]
    series = ["ninja", "flask", "drf"]
    values = {
        "sync":  {"ninja": sync["ninja"]["rps"], "flask": sync["flask"]["rps"], "drf": sync["drf"]["rps"]},
        # async DRF cell is adrf -- keep the DRF color, note it in the group label
        "async": {"ninja": asyn["ninja"]["rps"], "flask": asyn["flask"]["rps"], "drf": asyn["adrf"]["rps"]},
    }
    glabels = {"sync": ["sync  def", "(gunicorn / WSGI)"],
               "async": ["async  def", "(uvicorn / ASGI, DRF=adrf)"]}
    return grouped_bar_svg(
        "Parsing / validation JSON", "concurrency = 1, requests/sec (higher is better)",
        groups, series, values, theme, group_label_lines=glabels)


def chart_concurrency(theme):
    rl = json.load(open(os.path.join(BASE, "benchmark_results", "results_local.json")))
    conc = rl["concurrent"]
    workers = rl["workers_cases"]
    groups = [str(w) for w in workers]
    series = ["ninja", "flask", "drf"]
    values = {str(w): {fw: conc[fw][str(w)]["rps"] for fw in series} for w in workers}
    glabels = {g: [g] for g in groups}
    # Concurrency panel server stack (bench.py local): Ninja async on uvicorn,
    # sync Flask/DRF on uWSGI prefork (one request per worker).
    legend = {
        "ninja": "Django Ninja — uvicorn (async)",
        "flask": "Flask + marshmallow — uWSGI (sync)",
        "drf": "Django REST framework — uWSGI (sync)",
    }
    return grouped_bar_svg(
        "Calling a slow network operation", "concurrency = 50  —  Django Ninja uses async views",
        groups, series, values, theme, ylabel="requests / second",
        xlabel="number of worker processes", width=980, height=500, mb=110,
        value_labels=False, group_label_lines=glabels, legend_labels=legend)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ninja")
    ap.add_argument("--flask")
    ap.add_argument("--drf")
    ap.add_argument("--adrf")
    a = ap.parse_args()
    overrides = {k: v for k, v in vars(a).items() if v}  # applied to both themes

    os.makedirs(OUT, exist_ok=True)
    charts = (("parse_validate", chart_parse_validate), ("concurrency", chart_concurrency))
    for theme_name, base_theme in THEMES.items():
        theme = {**base_theme, "bars": {**base_theme["bars"], **overrides}}
        suffix = "" if theme_name == "light" else "-dark"
        for base, fn in charts:
            name = f"{base}{suffix}.svg"
            with open(os.path.join(OUT, name), "w") as f:
                f.write(fn(theme))
            print(f"wrote charts/{name}")


if __name__ == "__main__":
    main()
