#!/usr/bin/env python
"""Unified benchmark CLI — the three HTTP experiments behind one set of subcommands.

    uv run python bench.py local          # original two panels: parse/validate (c=1) + concurrency sweep
    uv run python bench.py server-matrix  # parse/validate as framework x {uWSGI, uvicorn}  (server confound)
    uv run python bench.py route-matrix   # parse/validate as sync def/gunicorn vs async def/uvicorn (incl. adrf)

Every subcommand writes the same benchmark_results/results_*.json that
make_charts.py already reads, so the charts keep working unchanged.

Not merged here on purpose: microbench_validate.py (validation CPU only, no HTTP,
one framework per process) and run_test.py (the original 2020 Docker + ab suite).
"""
import argparse
import os

import harness
from harness import Server, aggregate, env_for, network_service, oha, save_results

APP_PORT_DEFAULT = 8000
NS_PORT_DEFAULT = 9000
WORKERS_CASES = [1, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24]   # concurrency-panel x-axis
FRAMEWORK_NAMES = ("ninja", "flask", "drf")

# ---------------------------------------------------------------------------
# matrices  (cmd templates: {port} filled by the harness, {w} filled here)
# ---------------------------------------------------------------------------

# concurrency panel: sync apps on uWSGI prefork, Ninja async on uvicorn (as the original)
CONC_FRAMEWORKS = {
    "flask": ("uwsgi --http 127.0.0.1:{port} --module main:app --workers {w} --master --need-app --die-on-term",
              "app_flask_marshmallow"),
    "drf":   ("uwsgi --http 127.0.0.1:{port} --module drf.wsgi:application --workers {w} --master --need-app --die-on-term",
              "app_drf"),
    "ninja": ("uvicorn djninja.asgi:application --host 127.0.0.1 --port {port} --workers {w} --log-level warning",
              "app_ninja"),
}
# parse/validate panel: every framework on the SAME sync/WSGI server, so the number
# reflects framework+validation overhead, not the server model (Ninja on uWSGI here too).
C1_FRAMEWORKS = {
    "ninja": ("uwsgi --http 127.0.0.1:{port} --module djninja.wsgi:application --workers {w} --master --need-app --die-on-term",
              "app_ninja"),
    "flask": CONC_FRAMEWORKS["flask"],
    "drf":   CONC_FRAMEWORKS["drf"],
}

# parse/validate as framework x server
SERVER_MATRIX = {
    "ninja": {"sync":  ("uwsgi --http 127.0.0.1:{port} --module djninja.wsgi:application --workers 1 --master --need-app --die-on-term", "app_ninja"),
              "async": ("uvicorn djninja.asgi:application --host 127.0.0.1 --port {port} --workers 1 --log-level warning", "app_ninja")},
    "flask": {"sync":  ("uwsgi --http 127.0.0.1:{port} --module main:app --workers 1 --master --need-app --die-on-term", "app_flask_marshmallow"),
              "async": ("uvicorn asgi:application --host 127.0.0.1 --port {port} --workers 1 --log-level warning", "app_flask_marshmallow")},
    "drf":   {"sync":  ("uwsgi --http 127.0.0.1:{port} --module drf.wsgi:application --workers 1 --master --need-app --die-on-term", "app_drf"),
              "async": ("uvicorn drf.asgi:application --host 127.0.0.1 --port {port} --workers 1 --log-level warning", "app_drf")},
}

# parse/validate as route-style: sync def/gunicorn vs async def/uvicorn (adrf = genuinely-async DRF cell)
ROUTE_GROUPS = {
    "sync (def / gunicorn WSGI)": {
        "endpoint": "/api/create",
        "frameworks": {
            "ninja": ("gunicorn -w 1 -b 127.0.0.1:{port} djninja.wsgi:application", "app_ninja"),
            "flask": ("gunicorn -w 1 -b 127.0.0.1:{port} main:app", "app_flask_marshmallow"),
            "drf":   ("gunicorn -w 1 -b 127.0.0.1:{port} drf.wsgi:application", "app_drf"),
        },
    },
    "async (async def / uvicorn ASGI)": {
        "endpoint": "/api/create_async",
        "frameworks": {
            "ninja": ("uvicorn djninja.asgi:application --host 127.0.0.1 --port {port} --workers 1 --log-level warning", "app_ninja"),
            "flask": ("uvicorn asgi:application --host 127.0.0.1 --port {port} --workers 1 --log-level warning", "app_flask_marshmallow"),
            "adrf":  ("uvicorn drf.asgi:application --host 127.0.0.1 --port {port} --workers 1 --log-level warning", "app_drf"),
        },
    },
}


def app_url(port, endpoint):
    return f"http://127.0.0.1:{port}{endpoint}"


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def cmd_server_matrix(args):
    """Parse/validate at c=1 for every (framework, server) cell -> is the ranking server-independent?"""
    env = env_for(args.ns_port)
    url = app_url(args.app_port, "/api/create")
    results = {}
    with network_service(args.ns_port, env):
        for fw in FRAMEWORK_NAMES:
            results[fw] = {}
            for server in ("sync", "async"):
                tmpl, cwd = SERVER_MATRIX[fw][server]
                with Server(tmpl, cwd, app_port=args.app_port, env=env):
                    oha(url, 1, args.duration, "payload.json")              # warm
                    results[fw][server] = oha(url, 1, args.duration, "payload.json")["rps"]
                label = "uWSGI" if server == "sync" else "uvicorn"
                print(f"  {fw:6} {server:5} ({label}) -> {results[fw][server]:>8} rps", flush=True)
    path = save_results("results_server_matrix", results, args.output_dir)
    print("\n# parse/validate rps  (c=1)        all-sync   all-async")
    for fw in FRAMEWORK_NAMES:
        print(f"  {fw:8} : {results[fw]['sync']:>10} {results[fw]['async']:>11}")
    print(f"\nSaved -> {path}")


def cmd_route_matrix(args):
    """Parse/validate: sync def/gunicorn (WSGI) vs async def/uvicorn (ASGI), round-robin averaged."""
    env = env_for(args.ns_port)
    cells = [(g, fw) for g, spec in ROUTE_GROUPS.items() for fw in spec["frameworks"]]
    samples = {c: [] for c in cells}
    with network_service(args.ns_port, env):
        for rnd in range(1, args.rounds + 1):
            for g, fw in cells:
                tmpl, cwd = ROUTE_GROUPS[g]["frameworks"][fw]
                url = app_url(args.app_port, ROUTE_GROUPS[g]["endpoint"])
                with Server(tmpl, cwd, app_port=args.app_port, env=env):
                    oha(url, 1, args.duration, "payload.json")              # warm
                    r = oha(url, 1, args.duration, "payload.json")
                samples[(g, fw)].append(r)
                print(f"  r{rnd:>2} {g:34} {fw:6} -> {r['rps']:>8} rps  (ok={r['ok']})", flush=True)
    results = {}
    for (g, fw), ss in samples.items():
        results.setdefault(g, {})[fw] = aggregate(ss, include_p50=False)
    path = save_results("results_route_matrix", results, args.output_dir)
    print(f"\nSaved -> {path}  ({args.rounds} round(s), mean)")


def cmd_local(args):
    """Original two panels native: parse/validate (c=1, all on uWSGI) + slow-network concurrency sweep."""
    env = env_for(args.ns_port)

    def measure_cell(panel, fw, workers):
        if panel == "c1":
            tmpl, cwd = C1_FRAMEWORKS[fw]
            template = tmpl.format(port="{port}", w=workers)
            url = app_url(args.app_port, "/api/create")
            with Server(template, cwd, app_port=args.app_port, env=env):
                oha(url, 1, 1, "payload.json")                              # warm
                return oha(url, 1, args.duration_c1, "payload.json")
        tmpl, cwd = CONC_FRAMEWORKS[fw]
        template = tmpl.format(port="{port}", w=workers)
        url = app_url(args.app_port, "/api/iojob")
        with Server(template, cwd, app_port=args.app_port, env=env):
            oha(url, 1, 1)                                                  # warm
            return oha(url, 50, args.duration)

    # round-robin: frameworks interleaved (a/b/c/a/b/c) so a transient spike spreads
    # across frameworks instead of being charged to whichever ran at the time.
    cells = [("c1", fw, 1) for fw in FRAMEWORK_NAMES]
    for w in WORKERS_CASES:
        for fw in FRAMEWORK_NAMES:
            cells.append(("conc", fw, w))

    samples = {c: [] for c in cells}
    print(f"== {args.rounds} round(s), round-robin order, {len(cells)} cells/round ==", flush=True)
    with network_service(args.ns_port, env):
        for rnd in range(1, args.rounds + 1):
            for panel, fw, w in cells:
                r = measure_cell(panel, fw, w)
                samples[(panel, fw, w)].append(r)
                tag = "c1  create" if panel == "c1" else f"c50 w={w:<2}"
                print(f"  r{rnd:>2} {tag} {fw:6} -> {r['rps']:>8} rps (ok={r['ok']})", flush=True)

    c1 = {}
    conc = {fw: {} for fw in FRAMEWORK_NAMES}
    for (panel, fw, w), ss in samples.items():
        if panel == "c1":
            c1[fw] = aggregate(ss)
        else:
            conc[fw][w] = aggregate(ss)

    results = {"meta": {"date": os.environ.get("RUN_DATE", "unknown"),
                        "load_tool": "oha", "sync_server": "uwsgi", "async_server": "uvicorn",
                        "c1_server": "uwsgi (all frameworks, fair)",
                        "rounds": args.rounds, "duration_s": args.duration,
                        "order": "round-robin (framework-interleaved), mean of rounds"},
               "workers_cases": WORKERS_CASES, "c1": c1, "concurrent": conc}
    path = save_results("results_local", results, args.output_dir)

    print(f"\n# mean rps by worker count over {args.rounds} round(s) (slow network op, concurrency=50)")
    print("Framework  :" + "".join(f"{w:>9}" for w in WORKERS_CASES))
    for name in FRAMEWORK_NAMES:
        print(f"{name:<10} :" + "".join(f"{conc[name][w]['rps']:>9.0f}" for w in WORKERS_CASES))
    if args.rounds > 1:
        print("std        :" + "  (per-cell rps_std in results_local.json)")
    print("\nparse/validate (c=1): " + ", ".join(f"{fw} {c1[fw]['rps']}±{c1[fw]['rps_std']}" for fw in FRAMEWORK_NAMES))
    print(f"Saved -> {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_common(sp, *, duration_default):
    sp.add_argument("--rounds", type=int, default=int(os.environ.get("ROUNDS", "1")),
                    help="repeat the round-robin and average (>1 = 'slow' mode)")
    sp.add_argument("--duration", type=float, default=duration_default,
                    help="seconds of load per cell")
    sp.add_argument("--app-port", type=int, default=APP_PORT_DEFAULT)
    sp.add_argument("--ns-port", type=int, default=NS_PORT_DEFAULT)
    sp.add_argument("--output-dir", default=None, help="where to write results_*.json")
    sp.add_argument("--oha", default=None, help="path to the oha binary")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_local = sub.add_parser("local", help="original two panels, native (no Docker)")
    add_common(p_local, duration_default=float(os.environ.get("DURATION", "5")))
    p_local.add_argument("--duration-c1", type=float, default=float(os.environ.get("DURATION_C1", "3")),
                         help="seconds of load for the parse/validate (c=1) panel")
    p_local.set_defaults(func=cmd_local)

    p_sm = sub.add_parser("server-matrix", help="parse/validate x {uWSGI, uvicorn}")
    add_common(p_sm, duration_default=3.0)
    p_sm.set_defaults(func=cmd_server_matrix)

    p_rm = sub.add_parser("route-matrix", help="sync def/gunicorn vs async def/uvicorn (incl. adrf)")
    add_common(p_rm, duration_default=3.0)
    p_rm.set_defaults(func=cmd_route_matrix)

    args = parser.parse_args()
    if args.oha:
        harness.OHA = args.oha
    args.func(args)


if __name__ == "__main__":
    main()
