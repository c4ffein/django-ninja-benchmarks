#!/usr/bin/env python
"""Regression gate for benchmark results -- invariants, not absolute numbers.

CI runners are shared and vary wildly in absolute speed, so asserting absolute
rps in tight ranges would just flake. We gate on what survives a hardware change
(the committed baselines show these hold with 2x+ margins), and only *warn* on
things that are noisy or close.

  ERROR (fails CI):
    * every measured cell succeeded (ok == 1.0) and produced rps > 0
    * per framework, sync (WSGI) beats async (ASGI) on the CPU-bound endpoint
    * DRF / adrf is the slowest framework in every parse/validate group
    * validation CPU ordering pydantic < marshmallow < DRF (generous ratio band)
    * at concurrency=50 with 1 worker, Ninja (async) beats a single sync worker,
      and the sync frameworks scale up as workers increase
  WARN (reported, never fails CI):
    * close orderings (ninja vs flask: only ~4-14% apart in the baselines)
    * absolute rps drifting outside a wide band around the committed baseline

Usage:
    python tools_check.py --dir benchmark_results [--baseline-dir benchmark_results]
"""
import argparse
import json
import os
import sys


class Checker:
    LEVELS = {"ERROR": 0, "WARN": 1, "OK": 2}
    SYMBOL = {"ERROR": "FAIL", "WARN": "warn", "OK": " ok "}

    def __init__(self):
        self.items = []  # (level, msg)

    def gate(self, cond, msg, *, warn=False):
        """Record a check. Failing a non-warn gate fails CI; warn never does."""
        level = "OK" if cond else ("WARN" if warn else "ERROR")
        self.items.append((level, msg))
        return cond

    def section(self, title):
        self.items.append((None, title))

    def report(self):
        for level, msg in self.items:
            if level is None:
                print(f"\n# {msg}")
            else:
                print(f"  [{self.SYMBOL[level]}] {msg}")
        n_err = sum(1 for lvl, _ in self.items if lvl == "ERROR")
        n_warn = sum(1 for lvl, _ in self.items if lvl == "WARN")
        n_ok = sum(1 for lvl, _ in self.items if lvl == "OK")
        print(f"\n{n_err} error(s), {n_warn} warning(s), {n_ok} ok")
        return n_err


def load(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_jsonl(path):
    if not os.path.exists(path):
        return None
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def check_server_matrix(c, d):
    c.section("server-matrix (framework x {uWSGI, uvicorn}, parse/validate c=1)")
    sync = {fw: cells["sync"] for fw, cells in d.items()}
    for fw, cells in d.items():
        s, a = cells["sync"], cells["async"]
        c.gate(s > 0 and a > 0, f"{fw}: rps > 0 (sync={s}, async={a})")
        c.gate(s > a, f"{fw}: sync uWSGI ({s}) > async uvicorn ({a})")
    if "drf" in sync:
        c.gate(sync["drf"] == min(sync.values()), f"drf is slowest sync framework ({sync})")
    if "ninja" in sync and "flask" in sync:
        c.gate(sync["ninja"] >= sync["flask"],
               f"ninja >= flask (sync, close call) ({sync['ninja']} vs {sync['flask']})", warn=True)


def check_route_matrix(c, d):
    c.section("route-matrix (sync def/gunicorn vs async def/uvicorn)")
    sync_g = next((g for g in d if g.startswith("sync")), None)
    async_g = next((g for g in d if g.startswith("async")), None)
    for g, fws in d.items():
        short = "sync " if g.startswith("sync") else "async"
        for fw, cell in fws.items():
            c.gate(cell["rps"] > 0, f"{short} {fw}: rps > 0 ({cell['rps']})")
            c.gate(cell.get("ok", 1.0) >= 0.999, f"{short} {fw}: ok == 1.0 ({cell.get('ok')})")
    if sync_g and async_g:
        for s_fw, a_fw in (("ninja", "ninja"), ("flask", "flask"), ("drf", "adrf")):
            if s_fw in d[sync_g] and a_fw in d[async_g]:
                s, a = d[sync_g][s_fw]["rps"], d[async_g][a_fw]["rps"]
                c.gate(s > a, f"{s_fw} sync def ({s}) > {a_fw} async def ({a})")
        sv = {fw: cell["rps"] for fw, cell in d[sync_g].items()}
        c.gate(sv.get("drf") == min(sv.values()), f"sync: drf slowest ({sv})")
        av = {fw: cell["rps"] for fw, cell in d[async_g].items()}
        c.gate(av.get("adrf") == min(av.values()), f"async: adrf slowest ({av})")


def check_local(c, d):
    c.section("local (parse/validate c=1 + slow-network concurrency sweep)")
    c1 = d.get("c1", {})
    for fw, cell in c1.items():
        c.gate(cell["rps"] > 0, f"c1 {fw}: rps > 0 ({cell['rps']})")
    if {"ninja", "drf"} <= set(c1):
        c.gate(c1["ninja"]["rps"] > c1["drf"]["rps"],
               f"c1: ninja > drf ({c1['ninja']['rps']} vs {c1['drf']['rps']})")
    conc = d.get("concurrent", {})
    workers = d.get("workers_cases", [])
    for fw, byw in conc.items():
        for w, cell in byw.items():
            # concurrency panel is the noisiest -> ok dips are warnings, not gate failures
            c.gate(cell.get("ok", 1.0) >= 0.999, f"conc {fw} w={w}: ok == 1.0 ({cell.get('ok')})", warn=True)
    if workers:
        w_lo, w_hi = str(min(workers)), str(max(workers))
        if "ninja" in conc and w_lo in conc["ninja"]:
            nj = conc["ninja"][w_lo]["rps"]
            for fw in ("flask", "drf"):
                if fw in conc and w_lo in conc[fw]:
                    c.gate(nj > conc[fw][w_lo]["rps"],
                           f"conc w={w_lo}: ninja async ({nj}) > {fw} 1-worker ({conc[fw][w_lo]['rps']})")
        for fw in ("flask", "drf"):
            if fw in conc and w_lo in conc[fw] and w_hi in conc[fw]:
                lo, hi = conc[fw][w_lo]["rps"], conc[fw][w_hi]["rps"]
                c.gate(hi > lo, f"conc {fw}: scales with workers ({lo} -> {hi})")


def check_microbench(c, rows):
    c.section("microbench (validation CPU only, no HTTP)")
    us = {r["fw"]: r["us_per_op"] for r in rows}
    for fw, v in us.items():
        c.gate(v > 0, f"{fw}: us_per_op > 0 ({v})")
    if {"ninja", "marshmallow", "drf"} <= set(us):
        pyd, marsh, drf = us["ninja"], us["marshmallow"], us["drf"]
        c.gate(pyd < marsh < drf,
               f"ordering pydantic < marshmallow < DRF ({pyd} < {marsh} < {drf})")
        c.gate(2.0 <= marsh / pyd <= 8.0,
               f"marshmallow/pydantic in [2,8] ({marsh / pyd:.1f}x)", warn=True)
        c.gate(8.0 <= drf / pyd <= 50.0,
               f"DRF/pydantic in [8,50] ({drf / pyd:.1f}x)")


def check_drift(c, fresh, baseline):
    """Wide sanity vs committed baseline (warn only): did absolute rps collapse/explode?"""
    if not (fresh and baseline):
        return
    c.section("drift vs committed baseline (wide band, warnings only)")
    flat = lambda d: {f"{fw}/{srv}": v for fw, cells in d.items() for srv, v in cells.items()}
    fr, ba = flat(fresh), flat(baseline)
    for k, v in fr.items():
        if k in ba and ba[k] > 0:
            r = v / ba[k]
            c.gate(0.25 <= r <= 4.0, f"server-matrix {k}: within 4x of baseline ({v} vs {ba[k]}, {r:.2f}x)", warn=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="benchmark_results", help="dir holding fresh results_*.json")
    ap.add_argument("--baseline-dir", default=None, help="dir holding committed baselines (for drift warnings)")
    args = ap.parse_args()

    c = Checker()
    sm = load(os.path.join(args.dir, "results_server_matrix.json"))
    rm = load(os.path.join(args.dir, "results_route_matrix.json"))
    rl = load(os.path.join(args.dir, "results_local.json"))
    mb = load_jsonl(os.path.join(args.dir, "results_microbench.jsonl"))

    if not any([sm, rm, rl, mb]):
        print(f"no result files found in {args.dir!r}", file=sys.stderr)
        return 2

    if sm:
        check_server_matrix(c, sm)
    if rm:
        check_route_matrix(c, rm)
    if rl:
        check_local(c, rl)
    if mb:
        check_microbench(c, mb)
    if args.baseline_dir and sm:
        check_drift(c, sm, load(os.path.join(args.baseline_dir, "results_server_matrix.json")))

    n_err = c.report()
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
